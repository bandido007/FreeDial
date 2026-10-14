"""Periodic tasks (collected by config/schedule.py). Keep this file import-light."""

from celery.schedules import crontab

BEAT_SCHEDULE = {
    # Retry notifications that failed to send (within the attempt cap and
    # horizon) — every 15 minutes.
    "notifications-resend-failed": {
        "task": "notifications.resend_failed_notifications",
        "schedule": crontab(minute="*/15"),
    },
}
