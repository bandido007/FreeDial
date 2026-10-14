"""Who may sign in, and what a signed-in account may do before it complies.

Two kinds of gate:

* **Hard gates** — :func:`blocked_reason`. The account may not authenticate at
  all: deleted, deactivated, suspended, blocked, or a local account whose
  e-mail address was never proven. Checked at sign-in **and on every request**
  (see ``oauth_validator``), so flipping a gate takes effect on the next call.

* **Restrictions** — :func:`restriction`. The account may sign in, but must
  first do something: choose a new password (it expired, or an administrator
  required a change) or set up two-factor authentication (policy requires it).
  Until then the API answers only the operations needed to comply — see
  :mod:`apps.authentication.middleware`.
"""

from datetime import timedelta

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.enums import AccountStatusChoices, AuthProviderChoices
from apps.core.hooks import policy

# One wording for a deactivated account wherever it is refused: the password
# grant, the external sign-in and the bearer-token gate all say this.
ACCOUNT_DEACTIVATED_MESSAGE = (
    "Your account has been deactivated. Please contact the administrator for more information."
)

DEACTIVATED, UNVERIFIED, SUSPENDED, BLOCKED = 8010, 8011, 8012, 8013
MFA_CODE_REQUIRED = 8014
MFA_ENROLMENT_REQUIRED = 8015
PASSWORD_CHANGE_REQUIRED = 8016

_REQUEST_CACHE_ATTR = "_session_restriction"


def blocked_reason(user) -> tuple[int, str] | None:
    """Return ``(code, message)`` if authentication must be refused, else ``None``.

    The single source of truth for whether an account may authenticate, so a
    suspension takes effect everywhere at once. Ordered most-severe first.
    E-mail verification is required only for local (DB) accounts — external and
    directory identities arrive verified by their provider.
    """
    if getattr(user, "deleted", False) or not user.is_active:
        return DEACTIVATED, _(ACCOUNT_DEACTIVATED_MESSAGE)
    if user.account_status == AccountStatusChoices.BLOCKED.value:
        return BLOCKED, _("Your account has been blocked. Please contact the administrator.")
    if user.account_status == AccountStatusChoices.SUSPENDED.value:
        return SUSPENDED, _("Your account is suspended. Please contact the administrator.")
    if user.auth_provider == AuthProviderChoices.DB.value and not user.is_verified:
        return UNVERIFIED, _(
            "Please verify your email address before signing in. Check your inbox for the verification code."
        )
    return None


# ── Two-factor policy ────────────────────────────────────────────────────────
def mfa_required_for(user) -> bool:
    """Whether policy obliges this account to use two-factor authentication."""
    account_types = set(policy("MFA_REQUIRED_ACCOUNT_TYPES", []) or [])
    if user.account_type in account_types:
        return True
    role_names = {name.strip().lower() for name in (policy("MFA_REQUIRED_ROLES", []) or []) if name}
    if not role_names:
        return False
    from apps.rbac.selectors import user_role_names

    return bool(role_names & {name.lower() for name in user_role_names(user)})


# ── Password age ─────────────────────────────────────────────────────────────
def password_max_age() -> timedelta | None:
    try:
        days = int(policy("PASSWORD_MAX_AGE_DAYS", 0) or 0)
    except (TypeError, ValueError):
        days = 0
    return timedelta(days=days) if days > 0 else None


def password_expires_at(user):
    """When this account's password stops being accepted, or ``None`` for never."""
    max_age = password_max_age()
    if max_age is None or user.auth_provider != AuthProviderChoices.DB.value:
        return None
    if not user.has_usable_password():
        return None
    changed = user.password_changed_at or user.date_joined
    return changed + max_age


def password_expired(user) -> bool:
    expires = password_expires_at(user)
    return expires is not None and expires <= timezone.now()


# ── Restrictions ─────────────────────────────────────────────────────────────
def restriction(user, request=None) -> tuple[int, str] | None:
    """What a signed-in account must do before it may use the API, if anything.

    Evaluated once per request (it may need the account's roles).
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    if request is not None:
        cached = getattr(request, _REQUEST_CACHE_ATTR, None)
        if cached is not None and cached[0] == user.pk:
            return cached[1]

    outcome = _restriction(user)
    if request is not None:
        try:
            setattr(request, _REQUEST_CACHE_ATTR, (user.pk, outcome))
        except Exception:  # noqa: BLE001
            pass
    return outcome


def _restriction(user) -> tuple[int, str] | None:
    local = user.auth_provider == AuthProviderChoices.DB.value
    if local and (user.must_change_password or password_expired(user)):
        return PASSWORD_CHANGE_REQUIRED, _("Choose a new password to continue.")
    if not user.mfa_enabled and mfa_required_for(user):
        return MFA_ENROLMENT_REQUIRED, _(
            "Set up two-factor authentication to continue. Your account requires it."
        )
    return None


def forget_restriction(request) -> None:
    """Drop the per-request answer (after the account has just complied)."""
    if request is not None and hasattr(request, _REQUEST_CACHE_ATTR):
        try:
            delattr(request, _REQUEST_CACHE_ATTR)
        except Exception:  # noqa: BLE001
            pass
