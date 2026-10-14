"""Hardened GraphQL view.

* Enforces query limits — depth, complexity, aliases, top-level fields — before
  anything is executed (see :mod:`apps.core.graphql.limits`).
* Refuses array-batched requests unless enabled, and caps them when they are.
* Disables schema introspection when GraphiQL is off (i.e. in production).
* Wraps request-level errors in the standard response envelope so the client
  always receives ``{status, code, message, ...}``.
* Reports every failure to the API-error hook (the endpoint log listens).
"""

import logging

from django.conf import settings
from django.http import JsonResponse
from graphene_django.views import GraphQLView, HttpError
from graphql import ExecutionResult, FieldNode, GraphQLError, ValidationRule, parse, validate

from apps.core.graphql import limits
from apps.core.graphql.response import Response
from apps.core.hooks import report_api_error

logger = logging.getLogger(__name__)

INVALID_INPUT_CODE = 9002
REJECTED_CODE = 9004


class DisableIntrospectionRule(ValidationRule):
    """Reject ``__schema`` / ``__type`` introspection fields."""

    def enter_field(self, node: FieldNode, *_args):
        if node.name.value in ("__schema", "__type"):
            self.report_error(GraphQLError("Introspection is disabled on this server"))


class HardenedGraphQLView(GraphQLView):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Build the validation rule set once, from settings. (Set here rather
        # than as a property because the base view assigns self.validation_rules
        # during construction.)
        self.validation_rules = [] if settings.GRAPHIQL else [DisableIntrospectionRule]

    # ── batching ─────────────────────────────────────────────────────────
    def parse_body(self, request):
        data = super().parse_body(request)
        if isinstance(data, list):
            ceiling = int(getattr(settings, "GRAPHQL_MAX_BATCH_SIZE", 10) or 10)
            if len(data) > ceiling:
                raise HttpError(
                    JsonResponse(
                        {"error": f"A batch may hold at most {ceiling} operations."},
                        status=400,
                    )
                )
        return data

    # ── execution ────────────────────────────────────────────────────────
    def execute_graphql_request(self, request, data, query, variables, operation_name, show_graphiql=False):
        # Check up-front so a refusal is a friendly envelope instead of the
        # raw graphql error array.
        if query:
            try:
                document = parse(query)
            except GraphQLError as error:
                self._report_rejection(error, operation_name)
                return self._error_result(None, error.message, REJECTED_CODE)

            refusal = limits.check(document)
            if refusal:
                self._report_rejection(GraphQLError(refusal), operation_name)
                return self._error_result(self._top_field(document), refusal, INVALID_INPUT_CODE)

            if self.validation_rules:
                errors = validate(self.schema.graphql_schema, document, rules=self.validation_rules)
                if errors:
                    self._report_rejection(errors[0], operation_name)
                    return self._error_result(self._top_field(document), errors[0].message, REJECTED_CODE)

        result = super().execute_graphql_request(
            request, data, query, variables, operation_name, show_graphiql
        )
        self._report_execution_errors(result, operation_name)
        return self._envelope_request_errors(result, query)

    def _envelope_request_errors(self, result, query):
        """Turn *request-level* GraphQL errors into the standard envelope.

        Variable coercion ("Int cannot represent non-integer value: '2'"),
        unknown enum values and the like are raised by graphql-core before any
        resolver runs. They carry no ``path``, so graphene-django answers with
        HTTP 400 and a bare ``errors`` array — a shape a client that expects
        the envelope can only show as "Network error". Re-shape them as
        ``{status: false, code: 9002, message}`` on the operation's field,
        exactly as :func:`log_errors_and_warnings` does for
        :class:`ApplicationError`, so the real message reaches the form.

        The distinction is the one graphene-django itself applies when it picks
        the status code: an error raised *during* execution carries the field
        ``path`` it happened at; a request-level rejection has none. Resolver
        crashes (errors with a path) are left exactly as they are — they are
        backend defects that must keep their error array and the transaction
        rollback graphene-django performs for them.
        """
        if result is None or not getattr(result, "errors", None):
            return result
        request_level = [error for error in result.errors if not getattr(error, "path", None)]
        if not request_level:
            return result
        field_name = None
        try:
            field_name = self._top_field(parse(query))
        except Exception:  # noqa: BLE001 — the query already parsed once; be defensive anyway
            pass
        message = request_level[0].message
        logger.warning("[GraphQL] request rejected on field '%s': %s", field_name, message)
        envelope = Response.build_response(code=INVALID_INPUT_CODE, custom_message=message)
        return ExecutionResult(data={field_name: envelope} if field_name else None, errors=None)

    @staticmethod
    def _report_rejection(error, operation_name) -> None:
        """An up-front refusal is a handled failure (WARNING): malformed or
        oversized input, not a backend defect."""
        report_api_error(error, operation=operation_name or "", handled=True)

    @staticmethod
    def _report_execution_errors(result, operation_name) -> None:
        """Feed errors graphene caught *outside* the resolver decorators to the
        API-error hook. An error raised inside a resolver (it carries a field
        ``path`` and wraps the original exception) means a resolver crashed
        (backend defect → ERROR); a request-level error — malformed document,
        a variable that does not coerce — is a client issue (→ WARNING), even
        though graphql-core attaches the coercion failure as its
        ``original_error``."""
        if result is None or not getattr(result, "errors", None):
            return
        for error in result.errors:
            original = getattr(error, "original_error", None)
            request_level = not getattr(error, "path", None)
            report_api_error(
                original or error,
                operation=operation_name or "",
                handled=original is None or request_level,
            )

    def dispatch(self, request, *args, **kwargs):
        # When the IDE is disabled, only accept JSON POSTs.
        if not settings.GRAPHIQL:
            if request.method != "POST":
                return JsonResponse({"error": "GraphQL queries must be sent via POST"}, status=405)
            if request.content_type != "application/json":
                return JsonResponse({"error": "Only application/json is accepted"}, status=415)
        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def _top_field(document):
        try:
            for definition in document.definitions:
                selection_set = getattr(definition, "selection_set", None)
                if selection_set is None or getattr(definition, "operation", None) is None:
                    continue
                for selection in selection_set.selections:
                    if isinstance(selection, FieldNode):
                        return (selection.alias or selection.name).value
        except Exception:  # noqa: BLE001
            pass
        return None

    @staticmethod
    def _error_result(field_name, message, code):
        logger.warning("[GraphQL] rejected query on field '%s': %s", field_name, message)
        if not field_name:
            # Nothing to hang an envelope on (the document did not even parse):
            # answer in GraphQL's own error shape, so the client still gets
            # the reason instead of an empty body.
            return ExecutionResult(data=None, errors=[GraphQLError(message)])
        envelope = Response.build_response(code=code, custom_message=message)
        return ExecutionResult(data={field_name: envelope}, errors=None)
