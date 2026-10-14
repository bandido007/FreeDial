"""Read-side queries for the authentication domain (selectors never mutate)."""

from django.db.models import Q, QuerySet

from apps.authentication import gates
from apps.authentication.models import AuthenticationAudit, LdapConfig, UserLoginHistory
from apps.core.hooks import resolve_permissions
from apps.rbac.selectors import get_user_roles


def get_me_payload(user, request=None) -> dict:
    """The signed-in person's identity, classification and access.

    Returned as a plain dict so the GraphQL ``LoggedInUserType`` and the token
    response consume the same shape.
    """
    required = gates.restriction(user, request)
    expires = gates.password_expires_at(user)
    return {
        "attributes": user,
        "account_type": user.account_type,
        "auth_provider": user.auth_provider,
        "authorities": sorted(resolve_permissions(user, request)),
        "roles": list(get_user_roles(user)),
        "mfa_required": gates.mfa_required_for(user),
        "mfa_enrolment_required": bool(required and required[0] == gates.MFA_ENROLMENT_REQUIRED),
        "password_change_required": bool(required and required[0] == gates.PASSWORD_CHANGE_REQUIRED),
        "password_expires_at": expires,
        "restriction_code": required[0] if required else None,
    }


def login_history_queryset(filters: Q | None = None) -> QuerySet[UserLoginHistory]:
    qs = UserLoginHistory.objects.select_related("user").order_by("-logged_in_at")
    return qs.filter(filters) if filters is not None else qs


def my_login_history_queryset(user, filters: Q | None = None) -> QuerySet[UserLoginHistory]:
    """The signed-in person's own sign-ins — scoped server-side, always."""
    return login_history_queryset(filters).filter(user=user)


def authentication_events_queryset(filters: Q | None = None) -> QuerySet[AuthenticationAudit]:
    qs = AuthenticationAudit.objects.select_related("user").order_by("-created_at")
    return qs.filter(filters) if filters is not None else qs


def my_authentication_events_queryset(user, filters: Q | None = None) -> QuerySet[AuthenticationAudit]:
    return authentication_events_queryset(filters).filter(user=user)


def ldap_configs_queryset(filters: Q | None = None) -> QuerySet[LdapConfig]:
    qs = LdapConfig.objects.select_related("default_role")
    return qs.filter(filters) if filters is not None else qs
