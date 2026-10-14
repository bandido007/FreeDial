"""Logging helpers: request context on every record, JSON output for collectors.

* :class:`RequestContextFilter` stamps each record with the current request ID
  and acting user, so a text line reads ``[<request-id>]`` and a JSON line
  carries ``request_id`` / ``user_id`` — one search finds everything a request
  did, across the web process and the workers.
* :class:`JsonFormatter` renders one JSON object per line (what log collectors
  such as Loki, ELK and Cloud Logging parse without a custom pattern).

Dependency-free on purpose: ~40 lines beat another package to keep patched.
"""

import json
import logging
from datetime import UTC, datetime

from apps.core.context import get_current_user, get_request_id

# Attributes every LogRecord has; anything else was passed via `extra=`.
_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
    | {"message", "asctime", "request_id", "user_id", "taskName"}
)


class RequestContextFilter(logging.Filter):
    """Attach ``request_id`` and ``user_id`` to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        record.user_id = _user_id()
        return True


def _user_id():
    try:
        user = get_current_user()
        if user is not None and getattr(user, "is_authenticated", False):
            return str(getattr(user, "uid", None) or user.pk)
    except Exception:  # noqa: BLE001 — logging must never raise
        pass
    return None


class JsonFormatter(logging.Formatter):
    """One JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "user_id": getattr(record, "user_id", None),
            "environment": _environment(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def _environment() -> str | None:
    try:
        from django.conf import settings

        return getattr(settings, "ENVIRONMENT", None)
    except Exception:  # noqa: BLE001 — settings not configured yet
        return None
