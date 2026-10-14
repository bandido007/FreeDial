"""Shared, dependency-light helpers.

Highlights
----------
* :func:`generate_uuid7` — time-ordered UUIDv7 identifiers generated
  **in-process**. On Python 3.14+ it delegates to the stdlib ``uuid.uuid7``;
  on older interpreters it falls back to a spec-compliant (RFC 9562) pure-Python
  implementation. Either way there is no per-row database round-trip and no
  PostgreSQL extension to install.
* :func:`get_client_ip` — the caller's address, trusting ``X-Forwarded-For``
  only as far as the configured number of proxies.
* :class:`Utils` — small validation/string helpers, including a record of the
  most recent "missing required fields" so a generic 9005 error response can
  name exactly what was absent.
"""

import os
import re
import secrets
import string
import time
import uuid
from datetime import date, datetime, timedelta

from django.conf import settings

from apps.core import context

ALPHANUMERIC = string.ascii_uppercase + string.digits

# Resolved once at import time: the stdlib uuid7 if this interpreter has it.
_NATIVE_UUID7 = getattr(uuid, "uuid7", None)


def _uuid7_fallback() -> uuid.UUID:
    """RFC 9562 UUIDv7 for interpreters without ``uuid.uuid7`` (< 3.14).

    Layout (128 bits): 48-bit unix_ts_ms | 4-bit version | 12-bit rand_a |
    2-bit variant | 62-bit rand_b.
    """
    unix_ts_ms = time.time_ns() // 1_000_000
    rand_a = int.from_bytes(os.urandom(2), "big") & 0xFFF
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)

    value = (unix_ts_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76  # version 7
    value |= rand_a << 64
    value |= 0b10 << 62  # RFC 4122 variant
    value |= rand_b
    return uuid.UUID(int=value)


def generate_uuid7() -> uuid.UUID:
    """Return a time-ordered UUIDv7 — native on 3.14+, fallback otherwise."""
    if _NATIVE_UUID7 is not None:
        return _NATIVE_UUID7()
    return _uuid7_fallback()


def get_client_ip(request) -> str | None:
    """The caller's IP address.

    ``X-Forwarded-For`` is written by whoever sends the request, so its
    left-most entry is whatever the client claims. Only the entries appended by
    *our own* proxies can be believed: with ``TRUSTED_PROXY_COUNT = N`` the
    address N hops from the right is used. With ``0`` (no proxy) the header is
    ignored and the socket address is returned.
    """
    if request is None:
        return None
    remote = request.META.get("REMOTE_ADDR") or None
    proxies = int(getattr(settings, "TRUSTED_PROXY_COUNT", 0) or 0)
    if proxies <= 0:
        return remote
    hops = [hop.strip() for hop in (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",") if hop.strip()]
    if not hops:
        return remote
    return hops[-proxies] if len(hops) >= proxies else hops[0]


def login_identifier(request, credentials: dict | None = None) -> str | None:
    """The account a sign-in attempt is for, as django-axes should key it.

    axes defaults its lookup key to ``User.USERNAME_FIELD`` (``email``), but the
    OAuth2 password grant authenticates with a ``username`` credential — so the
    default finds nothing and the lockout silently never engages. This reads
    either spelling, from the credentials first and the posted form second,
    normalised so ``Asha@Example.org`` and ``asha@example.org`` count together.
    """
    sources = [credentials or {}]
    if request is not None:
        sources.append(getattr(request, "data", None) or getattr(request, "POST", None) or {})
    for source in sources:
        for key in ("username", "email"):
            value = source.get(key)
            if value:
                return str(value).strip().lower()
    return None


def strip_mount_prefix(path: str) -> str:
    """``path`` without the ``API_MOUNT_PREFIX`` the routes are also served under."""
    prefix = (getattr(settings, "API_MOUNT_PREFIX", "") or "").strip("/")
    if prefix and (path == f"/{prefix}" or path.startswith(f"/{prefix}/")):
        return path[len(prefix) + 1 :] or "/"
    return path


class Utils:
    """Stateless helpers used across the codebase."""

    # ── required-field validation ────────────────────────────────────────
    @staticmethod
    def empty_and_null_checker(obj_or_value, required_fields=None) -> bool:
        """Return True when nothing is missing.

        With ``required_fields`` it checks each named attribute on ``obj``;
        without it, it checks the single value. Missing names are recorded so
        :meth:`get_last_missing_fields` can surface them in the error message.
        """
        missing = Utils.get_missing_fields(obj_or_value, required_fields)
        Utils.set_last_missing_fields(missing)
        return len(missing) == 0

    @staticmethod
    def get_missing_fields(obj_or_value, required_fields=None) -> list:
        if required_fields:
            return [name for name in required_fields if getattr(obj_or_value, name, None) in (None, "")]
        if obj_or_value in (None, ""):
            return ["value"]
        return []

    @staticmethod
    def set_last_missing_fields(missing_fields) -> None:
        context.set_missing_fields(missing_fields)

    @staticmethod
    def get_last_missing_fields() -> list:
        return context.get_missing_fields()

    # ── small conversions ─────────────────────────────────────────────────
    @staticmethod
    def string_to_bool(value) -> bool:
        return str(value).strip().lower() in ("true", "1", "yes", "on")

    @staticmethod
    def trim(value):
        return str(value).strip() if value is not None else None

    @staticmethod
    def generate_otp(digits: int = 6) -> str:
        """A numeric one-time code from the OS's secure random source."""
        return str(secrets.randbelow(10**digits)).zfill(digits)

    @staticmethod
    def normalize_phone(value: str | None) -> str:
        """Light MSISDN tidy-up for SMS: strip spaces/dashes, keep a leading +.

        Not a validator (see :func:`apps.core.validators.normalize_phone`) —
        just removes whitespace/punctuation so ``+255 700 000 000`` becomes
        ``+255700000000`` before it hits the wire.
        """
        if not value:
            return ""
        cleaned = re.sub(r"[\s\-()]", "", str(value).strip())
        if cleaned.startswith("+"):
            return "+" + re.sub(r"\D", "", cleaned[1:])
        return re.sub(r"\D", "", cleaned)

    @staticmethod
    def generate_reference(prefix: str = "REF", length: int = 8) -> str:
        suffix = "".join(secrets.choice(ALPHANUMERIC) for _ in range(length))
        return f"{prefix}-{suffix}"

    @staticmethod
    def get_client_ip(request) -> str | None:
        return get_client_ip(request)


class DateUtils:
    """Date/time helpers (timezone-agnostic, calendar-based)."""

    @staticmethod
    def format_datetime(value: datetime) -> str:
        """e.g. ``June 25, 2026 02:37 PM``."""
        return value.strftime("%B %d, %Y %I:%M %p")

    @staticmethod
    def current_week_range():
        today = date.today()
        _, week_num, weekday = today.isocalendar()
        start = today - timedelta(days=weekday - 1)
        return {"week_number": week_num, "start_date": start, "end_date": start + timedelta(days=6)}

    @staticmethod
    def month_bounds(target: date | None = None):
        today = target or date.today()
        start = today.replace(day=1)
        if today.month == 12:
            next_month = today.replace(year=today.year + 1, month=1, day=1)
        else:
            next_month = today.replace(month=today.month + 1, day=1)
        return start, next_month - timedelta(days=1)


def camel_to_snake(name: str) -> str:
    """``meetingType`` -> ``meeting_type``; ``a.b`` -> ``a__b`` (ORM lookups)."""
    parts = name.split(".")
    snake_parts = []
    for part in parts:
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", part)
        snake_parts.append(re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower())
    return "__".join(snake_parts)
