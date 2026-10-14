"""Periodic tasks (collected by config/schedule.py). Keep this file import-light."""

from celery.schedules import crontab

BEAT_SCHEDULE = {
    # Temporary role grants stop counting the moment they expire; this only
    # tidies the rows away — every day at 00:40.
    "rbac-retire-expired-grants": {
        "task": "rbac.retire_expired_grants",
        "schedule": crontab(hour=0, minute=40),
    },
}
