"""GraphQL resolver/mutation decorators.

* :func:`authenticated` — require a logged-in user.
* :func:`has_permission` — require permission codes and/or account types.
* :func:`log_errors_and_warnings` — wrap the resolver in a transaction, convert
  raised :class:`ApplicationError` (and unexpected exceptions) into the standard
  failure envelope, and emit a log line.

All three return the right envelope *shape* — a paginated envelope for queries
that take a ``searchDto`` argument, otherwise the plain response envelope — so a
denied / failed paginated query still matches its declared GraphQL type.

Stacking order on a resolver (outermost first)::

    @has_permission(permissions=["can_view_users"])
    @log_errors_and_warnings
    def resolve_get_all_users_pageable(root, info, search_dto): ...

For classmethod mutations, ``@classmethod`` is the outermost decorator.
"""

import functools
import logging

from django.conf import settings
from django.db import transaction

from apps.core.context import get_request_id
from apps.core.exceptions import ApplicationError, PermissionDeniedError
from apps.core.graphql.pagination import Page
from apps.core.graphql.response import Response
from apps.core.graphql.status import resolve_message
from apps.core.hooks import report_api_error, resolve_permissions

logger = logging.getLogger(__name__)

UNEXPECTED_ERROR_CODE = 9004


def _extract_info(args):
    """The GraphQLResolveInfo is the argument carrying a ``.context``."""
    for arg in args:
        if hasattr(arg, "context"):
            return arg
    return None


def _is_pageable(info) -> bool:
    """True when the current field is a pageable query (declares searchDto)."""
    try:
        if getattr(info, "parent_type", None) and info.parent_type.name == "Query":
            variables = getattr(info, "variable_values", {}) or {}
            if "searchDto" in variables or "search_dto" in variables:
                return True
            # Inline (non-variable) arguments: look at the field definition.
            field = info.parent_type.fields.get(info.field_name)
            return bool(field and "searchDto" in field.args)
    except Exception:  # noqa: BLE001
        pass
    return False


def failure_envelope(info, code, message=None):
    """The failure envelope in the shape the current field declares: paginated
    for a pageable query, plain otherwise."""
    if _is_pageable(info):
        return Page.build_response(code=code, info=info, custom_message=message)
    return Response.build_response(code=code, info=info, custom_message=message)


_failure = failure_envelope


def _report_denial(info, message):
    """Surface an auth/permission denial as a handled (WARNING) failure in the
    request log — expected, but worth seeing when someone probes the API."""
    report_api_error(
        PermissionDeniedError(message),
        operation=getattr(info, "field_name", "?"),
        handled=True,
    )


def unexpected_error_message(exc, info=None) -> str:
    """What the client is told when a resolver crashes.

    The exception text can carry SQL, file paths or another person's data, so
    it is sent only where a developer is the reader (``EXPOSE_ERROR_DETAILS``,
    on in development). Everywhere else the client gets the catalogue message
    for 9004 plus the request ID — the one thing support needs to find the
    full traceback in the endpoint log.
    """
    if getattr(settings, "EXPOSE_ERROR_DETAILS", False):
        return f"Unexpected error: {exc}"
    message = resolve_message(UNEXPECTED_ERROR_CODE, info)
    request_id = get_request_id()
    return f"{message} (reference: {request_id})" if request_id else message


def authenticated():
    """Require an authenticated user; otherwise return a 9000 envelope."""

    def decorator(resolver):
        @functools.wraps(resolver)
        def wrapper(*args, **kwargs):
            info = _extract_info(args)
            user = getattr(getattr(info, "context", None), "user", None)
            if user is None or not user.is_authenticated:
                _report_denial(info, "Authentication required")
                return _failure(info, 9000, "[UNAUTHORIZED]: Authentication required")
            return resolver(*args, **kwargs)

        return wrapper

    return decorator


def has_permission(permissions=None, user_types=None, compare_both=False):
    """Guard a resolver by permission codes and/or account types.

    * ``compare_both=False`` (default): pass if the user has ANY required
      permission OR is one of the allowed account types.
    * ``compare_both=True``: require BOTH conditions.
    """
    permissions = permissions or []
    user_types = user_types or []

    def decorator(resolver):
        @functools.wraps(resolver)
        def wrapper(*args, **kwargs):
            info = _extract_info(args)
            request = getattr(info, "context", None)
            user = getattr(request, "user", None)

            if user is None or not user.is_authenticated:
                _report_denial(info, "Authentication required")
                return _failure(info, 9000, "[UNAUTHORIZED]: Authentication required")

            if not permissions and not user_types:
                _report_denial(info, "No access rule configured")
                return _failure(info, 9000, "[UNAUTHORIZED]: No access rule configured")

            user_perms = resolve_permissions(user, request) if permissions else set()
            has_perm = bool(set(permissions) & user_perms)
            has_type = getattr(user, "account_type", None) in user_types if user_types else False

            ok = (has_perm and has_type) if compare_both else (has_perm or has_type)
            if not ok:
                logger.warning(
                    "[has_permission] denied user=%s perms=%s types=%s",
                    getattr(user, "pk", None),
                    permissions,
                    user_types,
                )
                _report_denial(info, "Insufficient permissions")
                return _failure(info, 9000, "[UNAUTHORIZED]: Insufficient permissions")

            return resolver(*args, **kwargs)

        # Introspected by the permission guard test: every code used in a
        # decorator must exist in the catalogue.
        wrapper.required_permissions = tuple(permissions)
        return wrapper

    return decorator


def log_errors_and_warnings(resolver):
    """Run the resolver in a transaction and normalise any raised error."""

    @functools.wraps(resolver)
    def wrapper(*args, **kwargs):
        info = _extract_info(args)
        field = getattr(info, "field_name", "?")
        try:
            with transaction.atomic():
                return resolver(*args, **kwargs)
        except ApplicationError as exc:
            logger.warning("[%s] application error: %s", field, exc.message)
            report_api_error(exc, operation=field, handled=True)
            return _failure(info, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001 — convert to a stable envelope
            logger.exception("[%s] unexpected error", field)
            report_api_error(exc, operation=field, handled=False)
            return _failure(info, UNEXPECTED_ERROR_CODE, unexpected_error_message(exc, info))

    return wrapper
