"""Celery tasks for the notifications domain.

* :func:`send_notification_task` — deliver one queued notification (the async
  path used by :func:`apps.notifications.services.queue`). Idempotent: it skips
  rows already sent or past the attempt cap.
* :func:`resend_failed_notifications` — the periodic safety net (beat) that
  re-queues FAILED rows still within the attempt cap and the retry horizon.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.notifications import selectors, services
from apps.notifications.enums import NotificationStatusChoices
from apps.notifications.models import Notification

logger = logging.getLogger(__name__)

# Bounds for the resend sweep: let a fresh failure settle, give up after two days.
RESEND_SETTLING_MINUTES = 5
RESEND_HORIZON_DAYS = 2
RESEND_BATCH_SIZE = 200


def _max_attempts() -> int:
    return int(getattr(settings, "NOTIFICATION_MAX_ATTEMPTS", 5))


@shared_task(name="notifications.send_notification")
def send_notification_task(uid: str) -> bool:
    """Deliver one notification by uid. Returns True on success."""
    notification = Notification.objects.filter(uid=uid).first()
    if notification is None:
        logger.warning("[task:send_notification] %s not found", uid)
        return False
    if notification.is_sent or notification.status == NotificationStatusChoices.SKIPPED.value:
        return True
    if notification.attempts >= _max_attempts():
        logger.warning("[task:send_notification] %s exceeded max attempts", uid)
        return False
    _, ok = services.deliver(notification)
    return ok


@shared_task(name="notifications.resend_failed_notifications")
def resend_failed_notifications() -> int:
    """Re-queue FAILED notifications within the attempt cap and horizon.

    Scrubbed rows are left alone: their content is gone, so a retry would send
    the placeholder.
    """
    now = timezone.now()
    due = (
        selectors.notifications_queryset()
        .filter(
            status=NotificationStatusChoices.FAILED.value,
            is_sent=False,
            is_redacted=False,
            attempts__lt=_max_attempts(),
            created_at__lt=now - timedelta(minutes=RESEND_SETTLING_MINUTES),
            created_at__gt=now - timedelta(days=RESEND_HORIZON_DAYS),
        )
        .order_by("created_at")[:RESEND_BATCH_SIZE]
    )
    count = 0
    for notification in due:
        services.deliver(notification)
        count += 1
    if count:
        logger.info("[task:resend_failed_notifications] retried %s notification(s)", count)
    return count
