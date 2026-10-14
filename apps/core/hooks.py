"""Extension hooks — how ``core`` talks to apps it must not import.

``core`` sits at the bottom of the dependency graph: every app may import it,
and it imports none of them. Three things it needs from above are inverted
into hooks that the owning app fills in from its ``AppConfig.ready()``:

===================  =========================  ============================
Hook                 Filled in by               Used by
===================  =========================  ============================
permission resolver  ``apps.rbac``              ``@has_permission``
policy provider      ``apps.configuration``     ``policy()`` lookups
``api_error``        ``apps.audit`` (receiver)  decorators + the GraphQL view
===================  =========================  ============================

With a hook unfilled the behaviour is the safe one: no permissions, settings
defaults, and errors that are logged but not persisted.
"""

import logging
from collections.abc import Callable

from django.conf import settings
from django.dispatch import Signal

logger = logging.getLogger(__name__)

# ── API errors ───────────────────────────────────────────────────────────────
# Sent for every failure observed while serving an API call.
#   error     — the exception (or GraphQLError)
#   operation — GraphQL field / operation name, "" when unknown
#   handled   — True for an expected failure (bad input, denied, business rule),
#               False for a defect (unhandled exception)
api_error = Signal()


def report_api_error(error, *, operation: str = "", handled: bool = True) -> None:
    """Announce a failure. Never raises: observability must not break the API."""
    try:
        api_error.send_robust(sender=None, error=error, operation=operation, handled=handled)
    except Exception:  # noqa: BLE001
        logger.debug("[hooks] could not report an API error", exc_info=True)


# ── Permissions ──────────────────────────────────────────────────────────────
_permission_resolver: Callable | None = None
_REQUEST_CACHE_ATTR = "_resolved_permission_codes"


def set_permission_resolver(resolver: Callable) -> None:
    """Register ``resolver(user) -> set[str]`` (called once, by apps.rbac)."""
    global _permission_resolver
    _permission_resolver = resolver


def resolve_permissions(user, request=None) -> set[str]:
    """The permission codes ``user`` holds.

    Resolved at most once per request: a GraphQL document with ten guarded
    fields costs one lookup, not ten. Pass ``request`` (``info.context``) to
    get that; without it every call asks the resolver.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return set()
    if _permission_resolver is None:
        logger.warning("[hooks] no permission resolver registered; denying by default")
        return set()

    if request is not None:
        cached = getattr(request, _REQUEST_CACHE_ATTR, None)
        if cached is not None and cached[0] == user.pk:
            return cached[1]

    codes = set(_permission_resolver(user))
    if request is not None:
        try:
            setattr(request, _REQUEST_CACHE_ATTR, (user.pk, codes))
        except Exception:  # noqa: BLE001 — a request object that refuses attributes
            pass
    return codes


def forget_permissions(request) -> None:
    """Drop the per-request cache (after a mutation that changes roles)."""
    if request is not None and hasattr(request, _REQUEST_CACHE_ATTR):
        try:
            delattr(request, _REQUEST_CACHE_ATTR)
        except Exception:  # noqa: BLE001
            pass


# ── Runtime policy ───────────────────────────────────────────────────────────
_policy_provider: Callable | None = None
_MISSING = object()


def set_policy_provider(provider: Callable) -> None:
    """Register ``provider(name) -> value | None`` (called once, by apps.configuration)."""
    global _policy_provider
    _policy_provider = provider


def policy(name: str, default=None):
    """The effective value of a policy setting.

    Order: the value an administrator saved on the System settings screen →
    the Django setting of the same name → ``default``.
    """
    if _policy_provider is not None:
        try:
            value = _policy_provider(name)
        except Exception:  # noqa: BLE001 — a settings-table hiccup must not break login
            logger.warning("[hooks] policy provider failed for %s", name, exc_info=True)
            value = None
        if value is not None:
            return value
    value = getattr(settings, name, _MISSING)
    return default if value is _MISSING else value
