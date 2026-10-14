"""Account-lifecycle e-mails: the welcome after verification and the goodbye
after deletion. Best-effort like every notice — a mail outage must never fail
the flow that triggered it.

Both render from ``templates/emails/`` (the branded shell) with the person's
name filled in; the notification row keeps the audit trail.
"""

import logging

from django.conf import settings
from django.utils import timezone

from apps.notifications import services as notification_services
from apps.notifications.enums import NotificationChannelChoices
from apps.users.notifications import ACCOUNT_DELETED, WELCOME

logger = logging.getLogger(__name__)

WELCOME_TEMPLATE = "emails/welcome.html"
ACCOUNT_DELETED_TEMPLATE = "emails/account_deleted.html"


def _send(user, *, category: str, subject: str, message: str, template: str, **variables) -> bool:
    if not user.email:
        return False
    try:
        _, ok = notification_services.send_now(
            channel=NotificationChannelChoices.EMAIL.value,
            category=category,
            subject=subject,
            message=message,
            to_email=user.email,
            receiver=user,
            html_template=template,
            variables={"full_name": user.get_full_name() or user.email, **variables},
        )
    except Exception:  # noqa: BLE001 — a notice is never worth failing the transaction
        logger.exception("[account_notices] %s could not be sent to user %s", category, user.pk)
        return False
    logger.info("[account_notices] %s -> user %s ok=%s", category, user.pk, ok)
    return ok


def notify_welcome(user) -> bool:
    """ "Your account is ready" — sent once the account is verified. The code
    e-mail alone reads as "nothing happened yet"."""
    name = settings.PROJECT_NAME
    return _send(
        user,
        category=WELCOME,
        subject=f"Welcome to {name}",
        message=f"Your {name} account is verified and ready. Sign in to complete your profile.",
        template=WELCOME_TEMPLATE,
    )


def notify_account_deleted(user, *, by_admin: bool = False) -> bool:
    """Goodbye receipt, sent **before** the row is soft-deleted so the address
    is still the one on file."""
    name = settings.PROJECT_NAME
    return _send(
        user,
        category=ACCOUNT_DELETED,
        subject=f"Your {name} account has been deleted",
        message=(
            f"Your {name} account has been deleted"
            + (" by an administrator." if by_admin else " as you requested.")
            + " If this was not expected, contact the administrator."
        ),
        template=ACCOUNT_DELETED_TEMPLATE,
        deleted_at=timezone.localtime().strftime("%d %b %Y, %H:%M %Z"),
        by_admin=by_admin,
    )
