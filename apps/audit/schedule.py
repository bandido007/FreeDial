"""Periodic tasks (collected by config/schedule.py). Keep this file import-light."""

from celery.schedules import crontab

BEAT_SCHEDULE = {
    # E-mail yesterday's endpoint-error digest to the configured recipients —
    # every morning at 06:00 (skips itself when there is nothing to report).
    "audit-send-daily-error-report": {
        "task": "audit.send_daily_error_report",
        "schedule": crontab(hour=6, minute=0),
    },
    # Age out logs per severity (unresolved errors are never purged) — 02:00.
    "audit-purge-logs": {
        "task": "audit.purge_logs",
        "schedule": crontab(hour=2, minute=0),
    },
}
