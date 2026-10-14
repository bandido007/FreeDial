"""Endpoint transaction logging.

``RequestAuditMiddleware`` wraps every tracked request (``AUDIT_TRACKED_PREFIXES``)
in an audit context, then persists one :class:`~apps.audit.models.RequestLog`
row describing the transaction — the GraphQL operation(s) served, sanitised
variables, duration, HTTP status and a severity classified from what actually
happened inside:

* ``ERROR``   — an unhandled exception was reported into the context (by
  ``log_errors_and_warnings``, the hardened GraphQL view, or
  ``process_exception``) or the response is 5xx. A developer must fix it.
* ``WARNING`` — only expected failures (``ApplicationError``, 4xx, failed
  sign-in). Nothing to fix.
* ``INFO``    — completed normally. Persisted unless
  ``AUDIT_PERSIST_SUCCESS_REQUESTS`` is turned off.

Sits after ``CurrentUserMiddleware`` so ``request.user`` is fully resolved, and
persists in the response phase so it observes the final status code. Persisting
is strictly best-effort — a logging failure never breaks the request.
"""

import json
import logging
import re
import time

from django.conf import settings

from apps.audit import context
from apps.audit.constants import (
    DEFAULT_TRACKED_PREFIXES,
    SENSITIVE_MASK,
    SKIPPED_OPERATIONS,
    names_a_secret,
)
from apps.audit.enums import LogLevelChoices, RequestKindChoices
from apps.core.utils import strip_mount_prefix

logger = logging.getLogger(__name__)

_OPERATION_KIND_RE = re.compile(r"^\s*(query|mutation|subscription)\b", re.IGNORECASE)
_OPERATION_NAME_RE = re.compile(
    r"^\s*(?:query|mutation|subscription)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE
)
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MAX_VARIABLES_BYTES = 8192
MAX_GRAPHQL_BODY_BYTES = 1024 * 1024


def is_sensitive_key(key) -> bool:
    return names_a_secret(key)


def sanitize(value, depth=0):
    """Redact secret-looking keys and cap sizes so variables stay loggable."""
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {
            str(key): (SENSITIVE_MASK if is_sensitive_key(key) else sanitize(item, depth + 1))
            for key, item in list(value.items())[:30]
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(item, depth + 1) for item in list(value)[:20]]
    if isinstance(value, str) and len(value) > 300:
        return value[:300] + "…"
    return value


def top_level_fields(query: str) -> list[str]:
    """The depth-1 field names of a GraphQL document (tiny brace scanner)."""
    fields, depth, i, n = [], 0, 0, len(query)
    while i < n:
        ch = query[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 1 and (ch.isalpha() or ch == "_"):
            match = _IDENT_RE.match(query, i)
            if match:
                fields.append(match.group())
                i = match.end()
                # Skip the field's arguments/selection so aliases and nested
                # fields are not collected as extra top-level operations.
                while i < n and query[i] not in "{}":
                    i += 1
                continue
        i += 1
    return fields[:10]


def normalised_path(request) -> str:
    """The request path with the mount prefix removed."""
    return strip_mount_prefix(request.path_info or request.path)


class RequestAuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        prefixes = getattr(settings, "AUDIT_TRACKED_PREFIXES", None) or DEFAULT_TRACKED_PREFIXES
        self.tracked_prefixes = tuple(prefixes)

    def __call__(self, request):
        if not self._tracked(request):
            return self.get_response(request)

        ctx, token = context.begin_request(request)
        meta = self._describe(request)
        started = time.monotonic()
        try:
            response = self.get_response(request)
            duration_ms = int((time.monotonic() - started) * 1000)
            try:
                self._persist(request, response, ctx, meta, duration_ms)
            except Exception:  # noqa: BLE001 — logging must never break the API
                logger.warning("[RequestAuditMiddleware] failed to persist request log", exc_info=True)
            return response
        finally:
            context.end_request(token)

    def process_exception(self, request, exception):
        """Catch exceptions that escape the view layer entirely (true 500s)."""
        context.record_api_error(exception, operation=request.path, handled=False)
        return None  # let Django's normal 500 handling proceed

    # ── helpers ──────────────────────────────────────────────────────────
    def _tracked(self, request) -> bool:
        if request.method in ("OPTIONS", "HEAD"):
            return False
        path = normalised_path(request)
        if not path.startswith(self.tracked_prefixes):
            return False
        # GET /graphql is the GraphiQL IDE page, not an API transaction.
        return not (path.startswith("/graphql") and request.method != "POST")

    def _describe(self, request) -> dict:
        """Classify the transaction and capture sanitised inputs (request phase)."""
        path = normalised_path(request)
        if path.startswith("/graphql"):
            return self._describe_graphql(request)
        meta = {
            "kind": RequestKindChoices.Rest.value,
            "operations": path[:500],
            "operation_name": "",
            "variables": {},
        }
        # Token flows are form-encoded — safe to parse; a file upload may be
        # multipart, which is deliberately never touched.
        content_type = request.content_type or ""
        if request.method == "POST" and content_type == "application/x-www-form-urlencoded":
            try:
                meta["variables"] = sanitize(dict(request.POST.items()))
            except Exception:  # noqa: BLE001
                pass
        return meta

    @staticmethod
    def _describe_graphql(request) -> dict:
        kind = RequestKindChoices.Query.value
        operations, operation_name, variables = "", "", {}
        try:
            raw = request.body
            if len(raw) <= MAX_GRAPHQL_BODY_BYTES:
                body = json.loads(raw.decode("utf-8"))
                if isinstance(body, list):  # a batch: describe its first operation
                    body = body[0] if body else {}
                query = body.get("query") or ""
                operation_name = body.get("operationName") or ""
                if not operation_name:  # clients rarely send it; read it off the document
                    named = _OPERATION_NAME_RE.match(query)
                    operation_name = named.group(1) if named else ""
                match = _OPERATION_KIND_RE.match(query)
                if match and match.group(1).lower() == "mutation":
                    kind = RequestKindChoices.Mutation.value
                operations = ",".join(top_level_fields(query))[:500]
                sanitized = sanitize(body.get("variables") or {})
                if len(json.dumps(sanitized, default=str)) > MAX_VARIABLES_BYTES:
                    sanitized = {"_truncated": True}
                variables = sanitized
        except Exception:  # noqa: BLE001 — malformed body still gets logged
            pass
        return {
            "kind": kind,
            "operations": operations,
            "operation_name": operation_name[:255],
            "variables": variables,
        }

    @staticmethod
    def _persist(request, response, ctx, meta, duration_ms) -> None:
        status_code = getattr(response, "status_code", 0)
        if ctx.errors or status_code >= 500:
            level = LogLevelChoices.ERROR.value
        elif ctx.warnings or status_code >= 400:
            level = LogLevelChoices.WARNING.value
        else:
            level = LogLevelChoices.INFO.value

        if meta["operation_name"] in SKIPPED_OPERATIONS or meta["operations"].startswith("__"):
            return
        if level == LogLevelChoices.INFO.value and not getattr(
            settings, "AUDIT_PERSIST_SUCCESS_REQUESTS", True
        ):
            return

        user = getattr(request, "user", None)
        if user is not None and not getattr(user, "is_authenticated", False):
            user = None
        first_error = ctx.errors[0] if ctx.errors else None

        from apps.audit import services

        services.record_request_log(
            request_id=ctx.request_id,
            kind=meta["kind"],
            operations=meta["operations"],
            operation_name=meta["operation_name"],
            method=request.method,
            path=normalised_path(request)[:255],
            user=user,
            status_code=status_code,
            level=level,
            duration_ms=duration_ms,
            error_type=(first_error or {}).get("type", ""),
            error_message=(first_error or {}).get("message", ""),
            traceback="\n\n".join(e.get("traceback", "") for e in ctx.errors).strip(),
            errors=ctx.errors + ctx.warnings,
            variables=meta["variables"],
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
