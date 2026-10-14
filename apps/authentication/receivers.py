"""What authentication does when the users app announces something.

``users`` cannot call this app (it sits below it), so it sends signals and the
reactions live here — see :mod:`apps.users.signals`.
"""

import logging

from django.utils import timezone

from apps.audit import services as audit_services
from apps.authentication import events, password_services, tokens
from apps.authentication.enums import STATUS_FAILURE, AuthEventChoices
from apps.authentication.models import AuthenticationAudit, UserLoginHistory
from apps.users import data_export, signals

logger = logging.getLogger(__name__)


def _on_registered(sender, user=None, ip_address=None, user_agent="", **kwargs):
    events.record_authentication_event(
        event_type=AuthEventChoices.Registered, user=user, ip_address=ip_address, user_agent=user_agent
    )


def _on_invited(sender, user=None, invited_by=None, **kwargs):
    return {"email_ok": password_services.send_invitation(user, invited_by=invited_by)}


def _on_gated(sender, user=None, reason="", **kwargs):
    """A held account keeps no usable credential: sessions and pending links go."""
    revoked = tokens.revoke_all_sessions(user=user)
    password_services.retire_tokens(user)
    events.record_authentication_event(
        event_type=AuthEventChoices.SessionsRevoked,
        user=user,
        metadata={"revoked": revoked, "reason": reason},
    )


def _on_email_changed(sender, user=None, old_email="", **kwargs):
    """The address is the recovery channel: changing it ends every session and
    every link that was mailed to the old address."""
    revoked = tokens.revoke_all_sessions(user=user)
    password_services.retire_tokens(user)
    events.record_authentication_event(
        event_type=AuthEventChoices.EmailChanged,
        user=user,
        metadata={"old_email": old_email, "revoked": revoked},
    )


def _login_history(user) -> list[dict]:
    return [
        {
            "at": str(entry.logged_in_at),
            "ip": entry.ip_address,
            "device": entry.label,
            "provider": entry.provider,
        }
        for entry in user.login_history.all()[:200]
    ]


def _security_events(user) -> list[dict]:
    return [
        {
            "at": str(entry.created_at),
            "event": entry.event_type,
            "status": entry.status,
            "ip": entry.ip_address,
        }
        for entry in user.auth_audits.all()[:200]
    ]


def _external_identities(user) -> list[dict]:
    return [
        {"provider": identity.provider, "linked_at": str(identity.created_at)}
        for identity in user.external_identities.all()
    ]


def _logins_today() -> int:
    return UserLoginHistory.objects.filter(logged_in_at__date=timezone.localdate()).count()


def _failed_logins_today() -> int:
    return AuthenticationAudit.objects.filter(
        created_at__date=timezone.localdate(),
        status=STATUS_FAILURE,
        event_type__in=[AuthEventChoices.FailedLogin.value, AuthEventChoices.AccountLocked.value],
    ).count()


def _connect_directory_signal() -> None:
    """The global directory backend (django-auth-ldap) announces each sign-in."""
    try:
        from django_auth_ldap.backend import populate_user
    except ImportError:
        logger.debug("django-auth-ldap not installed; directory provisioning signal skipped")
        return

    from apps.authentication.directory_services import on_directory_login

    def _on_populate_user(sender, user=None, ldap_user=None, **kwargs):
        on_directory_login(user)

    populate_user.connect(_on_populate_user, dispatch_uid="authentication-ldap-populate-user", weak=False)


def connect() -> None:
    signals.user_registered.connect(_on_registered, dispatch_uid="authentication-user-registered")
    signals.user_invited.connect(_on_invited, dispatch_uid="authentication-user-invited")
    signals.account_gated.connect(_on_gated, dispatch_uid="authentication-account-gated")
    signals.email_changed.connect(_on_email_changed, dispatch_uid="authentication-email-changed")

    data_export.register_section("login_history", _login_history)
    data_export.register_section("security_events", _security_events)
    data_export.register_section("external_sign_in", _external_identities)

    audit_services.register_stat("logins_today", _logins_today)
    audit_services.register_stat("failed_logins_today", _failed_logins_today)

    _connect_directory_signal()
