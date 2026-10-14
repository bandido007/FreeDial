"""Per-request audit context.

The request-log middleware opens a context at the start of every tracked
request; anything that happens *inside* the request — a resolver converting an
exception into the failure envelope, graphene catching an error the decorators
never saw — reports into it through the ``api_error`` hook of
:mod:`apps.core.hooks`. When the response is ready, the middleware reads the
accumulated errors/warnings to classify the transaction (INFO / WARNING /
ERROR) and persist one :class:`~apps.audit.models.RequestLog` row.

The same context hands the model-change receivers (``apps.audit.signals``) the
client address, so every ``AuditLog`` row written during a request can be
traced back to the exact API transaction that caused it. The request ID itself
comes from :mod:`apps.core.context`, so logs, the change trail and the endpoint
log all carry the same one.
"""

import traceback
from contextvars import ContextVar
from dataclasses import dataclass, field

from apps.core.context import get_request_id
from apps.core.hooks import api_error
from apps.core.utils import get_client_ip

_context: ContextVar = ContextVar("audit_context", default=None)

MAX_MESSAGE = 2000
MAX_TRACEBACK = 20000


@dataclass
class RequestAuditContext:
    request_id: str = ""
    ip_address: str | None = None
    user_agent: str = ""
    # Unexpected failures — a developer has to fix these (level ERROR).
    errors: list[dict] = field(default_factory=list)
    # Expected failures — business-rule violations, bad input (level WARNING).
    warnings: list[dict] = field(default_factory=list)


def begin_request(request):
    """Open the context; returns ``(context, token)`` for :func:`end_request`."""
    ctx = RequestAuditContext(
        request_id=getattr(request, "request_id", None) or get_request_id() or "",
        ip_address=get_client_ip(request) or None,
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )
    return ctx, _context.set(ctx)


def get_audit_context() -> RequestAuditContext | None:
    return _context.get()


def end_request(token) -> None:
    _context.reset(token)


def record_api_error(exc, *, operation: str | None = None, handled: bool) -> None:
    """Report an exception raised while serving the current request.

    ``handled=True``  → an expected :class:`~apps.core.exceptions.ApplicationError`
    (or GraphQL validation error); classified WARNING.
    ``handled=False`` → an unexpected exception; classified ERROR and captured
    with its traceback so the Endpoint errors screen shows exactly what to fix.

    Never raises — auditing must not be able to break the API it observes.
    """
    try:
        ctx = get_audit_context()
        if ctx is None:  # outside a tracked request (a task, the shell, a test)
            return
        entry = {
            "type": exc.__class__.__name__,
            "message": str(exc)[:MAX_MESSAGE],
            "operation": operation or "",
            "handled": handled,
        }
        if handled:
            ctx.warnings.append(entry)
            return
        tb = ""
        if getattr(exc, "__traceback__", None) is not None:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        entry["traceback"] = tb[-MAX_TRACEBACK:]
        ctx.errors.append(entry)
    except Exception:  # noqa: BLE001 — auditing is strictly best-effort
        pass


def _on_api_error(sender, error=None, operation="", handled=True, **kwargs):
    record_api_error(error, operation=operation, handled=handled)


def connect() -> None:
    """Listen to the failures the API layer announces (see apps.core.hooks)."""
    api_error.connect(_on_api_error, dispatch_uid="audit-api-error")
