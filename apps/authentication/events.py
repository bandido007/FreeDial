"""The authentication audit: what happened, to whom, from where.

Every security-relevant event lands in
:class:`~apps.authentication.models.AuthenticationAudit` through
:func:`record_authentication_event`; every successful sign-in also lands in
:class:`~apps.authentication.models.UserLoginHistory` through
:func:`record_login`.

Recording never raises: an audit that cannot be written must not stop a person
signing in.

A **failure** is usually recorded on the way out of a resolver whose
transaction is about to be rolled back — which would erase the record of the
failure with it. Failures are therefore written once the request has finished
(see :func:`apps.core.context.after_request`).
"""

import logging

from django.conf import settings
from django.contrib.auth.models import update_last_login
from django.db import connection
from django.utils import timezone

from apps.authentication.enums import STATUS_FAILURE, STATUS_SUCCESS, AuthEventChoices
from apps.authentication.models import AuthenticationAudit, UserLoginHistory
from apps.authentication.notifications import NEW_DEVICE_LOGIN
from apps.core.context import after_request, get_request_id
from apps.core.enums import to_values
from apps.core.utils import get_client_ip
from apps.notifications import routes
from apps.notifications import services as notification_services
from apps.notifications.enums import NotificationChannelChoices

logger = logging.getLogger(__name__)

NEW_DEVICE_TEMPLATE = "emails/new_device_login.html"


def request_origin(request) -> tuple[str | None, str]:
    """(client IP, user agent) of ``request``."""
    if request is None:
        return None, ""
    return get_client_ip(request), request.META.get("HTTP_USER_AGENT", "")


def record_authentication_event(
    *,
    event_type,
    status: str = STATUS_SUCCESS,
    email: str = "",
    user=None,
    ip_address=None,
    user_agent: str = "",
    error_message: str = "",
    metadata: dict | None = None,
    request=None,
) -> None:
    """Persist an authentication-audit row; never let auditing break sign-in."""
    if request is not None and ip_address is None and not user_agent:
        ip_address, user_agent = request_origin(request)
    row = {
        "user": user if getattr(user, "pk", None) else None,
        "email": email or (getattr(user, "email", "") if user else ""),
        "event_type": to_values(event_type),
        "status": status,
        "ip_address": ip_address or None,
        "user_agent": user_agent or "",
        "error_message": error_message or "",
        "metadata": metadata or {},
        "request_id": get_request_id() or "",
        "created_at": timezone.now(),
    }

    def write() -> None:
        try:
            AuthenticationAudit.objects.create(**row)
        except Exception:  # noqa: BLE001
            logger.exception("[record_authentication_event] could not record %s", row["event_type"])

    if status == STATUS_FAILURE and connection.in_atomic_block and after_request(write):
        return
    write()


def record_failure(*, event_type=AuthEventChoices.FailedLogin, **kwargs) -> None:
    record_authentication_event(event_type=event_type, status=STATUS_FAILURE, **kwargs)


def record_login(user, request, *, provider: str = "", grant_type: str = "", metadata=None) -> None:
    """A successful sign-in: stamp ``last_login``, write the history row, audit
    it, and warn the owner when the device is one not seen before."""
    update_last_login(None, user)  # type: ignore[arg-type]  # the sender is unused
    entry = None
    try:
        entry = UserLoginHistory.record(user, request, provider=provider)
    except Exception:  # noqa: BLE001
        logger.exception("[record_login] could not record the login history for user %s", user.pk)

    record_authentication_event(
        event_type=AuthEventChoices.TokenGenerated,
        user=user,
        request=request,
        metadata={
            "grant_type": grant_type,
            "provider": provider,
            "new_device": bool(entry and entry.is_new_device),
            **(metadata or {}),
        },
    )
    if entry is not None and entry.is_new_device:
        notify_new_device(user, entry)


def notify_new_device(user, entry: UserLoginHistory) -> bool:
    """Tell the account's owner about a sign-in from a device not seen before."""
    if not getattr(settings, "NEW_DEVICE_LOGIN_ALERT", True) or not user.email:
        return False
    domain = getattr(settings, "FRONTEND_DOMAIN", "").rstrip("/")
    project = settings.PROJECT_NAME
    try:
        notification_services.queue(
            channel=NotificationChannelChoices.EMAIL.value,
            category=NEW_DEVICE_LOGIN,
            subject=f"New sign-in to your {project} account",
            message=(
                f"Your {project} account was signed in to from a new device ({entry.label}). "
                "If this was not you, change your password now."
            ),
            receiver=user,
            html_template=NEW_DEVICE_TEMPLATE,
            variables={
                "full_name": user.get_full_name() or user.email,
                "signed_in_at": timezone.localtime(entry.logged_in_at).strftime("%d %b %Y, %H:%M %Z"),
                "device": entry.label,
                "ip_address": entry.ip_address or "",
                "security_url": f"{domain}{routes.MY_SECURITY}",
                "project": project,
            },
            url=routes.MY_SECURITY,
        )
        return True
    except Exception:  # noqa: BLE001
        logger.exception("[notify_new_device] could not queue the alert for user %s", user.pk)
        return False
