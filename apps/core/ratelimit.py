"""Lightweight, cache-backed rate limiting for the public (unauthenticated) surface.

django-axes guards the *login* endpoint, but registration, OTP issue/resend and
password-reset are unauthenticated with no throttle of their own — an open door
for enumeration and OTP/e-mail flooding. This module adds a small fixed-window
counter on top of the Django cache (Redis in production, locmem in tests) so
those endpoints get a ceiling without any new infrastructure.

Named scopes keep the numbers in one place (``RATE_LIMITS`` in settings)::

    from apps.core.ratelimit import throttle_scope
    throttle_scope("register", ip)            # limit + window from settings
    throttle_scope("otp", email)

or spell the limit out at the call site::

    from apps.core.ratelimit import throttle
    throttle("export", user.pk, limit=10, window=60)

Each call increments the counter and raises :class:`ThrottledError` once the
limit is exceeded. Keys are hashed so raw e-mails/IPs never land in the cache.
"""

import hashlib

from django.conf import settings
from django.core.cache import cache

from apps.core.exceptions import ThrottledError

_PREFIX = "rl"
_DEFAULT_LIMIT = (30, 60)


def _key(scope: str, identifier: str) -> str:
    digest = hashlib.sha256(str(identifier or "").strip().lower().encode()).hexdigest()[:32]
    return f"{_PREFIX}:{scope}:{digest}"


def throttle(scope: str, identifier, *, limit: int, window: int, message: str | None = None) -> int:
    """Count one hit for ``(scope, identifier)``; raise once ``limit`` is passed.

    ``window`` is the fixed window length in seconds. Returns the current count
    (useful for logging). A blank ``identifier`` is a no-op (nothing to key on).
    """
    if not identifier:
        return 0
    key = _key(scope, identifier)
    try:
        # add() only sets when the key is absent, so the first hit in a window
        # starts the TTL; subsequent hits increment without extending it.
        cache.add(key, 0, timeout=window)
        count = cache.incr(key)
    except ValueError:
        # The key expired between add() and incr() — restart the window.
        cache.set(key, 1, timeout=window)
        count = 1

    if count > limit:
        raise ThrottledError(
            message or "Too many attempts. Please wait a few minutes and try again.",
            code=9018,
        )
    return count


def limit_for(scope: str) -> tuple[int, int]:
    """``(limit, window_seconds)`` configured for ``scope``."""
    limits = getattr(settings, "RATE_LIMITS", None) or {}
    limit, window = limits.get(scope, _DEFAULT_LIMIT)
    return int(limit), int(window)


def throttle_scope(scope: str, identifier, *, message: str | None = None) -> int:
    """:func:`throttle` with the limit and window named ``scope`` in settings."""
    limit, window = limit_for(scope)
    return throttle(scope, identifier, limit=limit, window=window, message=message)


def reset(scope: str, identifier) -> None:
    """Clear a counter (e.g. after a successful, legitimate action)."""
    if identifier:
        cache.delete(_key(scope, identifier))
