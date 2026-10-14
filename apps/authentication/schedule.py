"""Periodic tasks (collected by config/schedule.py). Keep this file import-light."""

from celery.schedules import crontab

BEAT_SCHEDULE = {
    # Expired tokens, used reset links and old password-history rows — 02:20.
    "authentication-purge-expired": {
        "task": "authentication.purge_expired",
        "schedule": crontab(hour=2, minute=20),
    },
}
