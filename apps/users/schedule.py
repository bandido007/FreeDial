"""Periodic tasks (collected by config/schedule.py). Keep this file import-light."""

from celery.schedules import crontab

BEAT_SCHEDULE = {
    # Purge abandoned never-verified self-registrations (and stale code rows)
    # so their email/phone are freed — every day at 02:10.
    "users-purge-stale-unverified-accounts": {
        "task": "users.purge_stale_unverified_accounts",
        "schedule": crontab(hour=2, minute=10),
    },
}
