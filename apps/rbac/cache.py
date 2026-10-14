"""Cache for resolved permission sets.

Checking a permission means "union of the permissions on every role this user
holds" — a four-table join. It is asked on every guarded GraphQL field, so the
answer is cached per user.

Invalidation uses a **version counter** rather than deleting keys: every cache
key embeds the current version, and any change to roles, role permissions or
grants bumps it. All previously cached sets become unreachable at once and age
out on their own. One integer increment, no key enumeration, correct across
every worker process that shares the cache.

The time-to-live (``RBAC_PERMISSION_CACHE_SECONDS``) is only the safety net for
changes that bypass signals and for grants that expire by date.
"""

from django.conf import settings
from django.core.cache import cache

_VERSION_KEY = "rbac:version"
_PREFIX = "rbac:perms"
_VERSION_TTL = None  # the counter itself never expires


def _version() -> int:
    version = cache.get(_VERSION_KEY)
    if version is None:
        cache.add(_VERSION_KEY, 1, timeout=_VERSION_TTL)
        version = cache.get(_VERSION_KEY) or 1
    return int(version)


def _key(user_pk) -> str:
    return f"{_PREFIX}:{_version()}:{user_pk}"


def ttl() -> int:
    return int(getattr(settings, "RBAC_PERMISSION_CACHE_SECONDS", 300) or 0)


def get(user_pk) -> set[str] | None:
    if ttl() <= 0:
        return None
    cached = cache.get(_key(user_pk))
    return set(cached) if cached is not None else None


def put(user_pk, codes: set[str]) -> None:
    if ttl() > 0:
        cache.set(_key(user_pk), sorted(codes), timeout=ttl())


def invalidate_all() -> None:
    """Make every cached permission set unreachable."""
    try:
        cache.incr(_VERSION_KEY)
    except ValueError:
        cache.set(_VERSION_KEY, 2, timeout=_VERSION_TTL)
