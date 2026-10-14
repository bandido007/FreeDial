"""Request-scoped context: who is acting, and which request this is.

The ORM layer (``BaseModel.save`` / ``delete``) needs to know *who* is acting
to stamp ``created_by`` / ``updated_by`` without every call site threading the
user through. Log lines and audit rows need the request ID for the same reason.

Both are held in :mod:`contextvars`, not ``threading.local``: a context
variable is isolated per thread **and** per asyncio task, so the value is
correct under WSGI, ASGI and inside Celery workers alike.

Outside a request there is no current user. Code that writes on someone's
behalf — a Celery task, a management command — says so explicitly::

    with acting_as(user):
        invoice.save()          # created_by / updated_by = user

Writes that must survive a rollback
-----------------------------------
A resolver runs inside a transaction, and a failure rolls it back — including
any "this attempt failed" row written on the way out. :func:`after_request`
postpones such a write until the request has finished, when no transaction of
the request is open any more::

    def _write():
        AuthenticationAudit.objects.create(...)

    if not after_request(_write):   # no request in progress → write now
        _write()
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar

_current_user: ContextVar = ContextVar("current_user", default=None)
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_missing_fields: ContextVar[tuple] = ContextVar("missing_fields", default=())
_after_request: ContextVar[list | None] = ContextVar("after_request", default=None)

logger = logging.getLogger(__name__)


# ── Acting user ──────────────────────────────────────────────────────────────
def get_current_user():
    """The user this unit of work is attributed to, or ``None``."""
    return _current_user.get()


def set_current_user(user):
    """Set the acting user; returns a token for :func:`reset_current_user`."""
    return _current_user.set(user)


def reset_current_user(token) -> None:
    _current_user.reset(token)


@contextmanager
def acting_as(user):
    """Attribute every write inside the block to ``user``."""
    token = _current_user.set(user)
    try:
        yield user
    finally:
        _current_user.reset(token)


# ── Request ID ───────────────────────────────────────────────────────────────
def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(request_id: str | None):
    """Set the request ID; returns a token for :func:`reset_request_id`."""
    return _request_id.set(request_id)


def reset_request_id(token) -> None:
    _request_id.reset(token)


# ── "Which required fields were missing" (feeds the 9005 message) ───────────
def get_missing_fields() -> list:
    return list(_missing_fields.get())


def set_missing_fields(fields) -> None:
    _missing_fields.set(tuple(fields or ()))


# ── Work postponed to the end of the request ─────────────────────────────────
def after_request(callback) -> bool:
    """Run ``callback`` once the current request has been answered.

    Returns ``False`` when no request is in progress (a task, a command, a
    direct call in a test) — the caller then runs the work immediately.
    """
    queue = _after_request.get()
    if queue is None:
        return False
    queue.append(callback)
    return True


def open_after_request_queue():
    """Start collecting postponed work; returns a token for :func:`run_after_request_queue`."""
    return _after_request.set([])


def run_after_request_queue(token) -> None:
    """Run everything postponed during the request, then close the queue.

    Each callback is isolated: one that fails is logged and the rest still run.
    """
    queue = _after_request.get() or []
    _after_request.reset(token)
    for callback in queue:
        try:
            callback()
        except Exception:  # noqa: BLE001
            logger.exception("[context] postponed work failed")
