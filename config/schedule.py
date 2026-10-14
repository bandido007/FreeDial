"""Celery beat schedule, composed from each app's own ``schedule.py``.

An app declares its periodic tasks next to the tasks themselves::

    # apps/<app>/schedule.py
    from celery.schedules import crontab

    BEAT_SCHEDULE = {
        "purge-request-logs-daily": {
            "task": "audit.purge_request_logs",
            "schedule": crontab(hour=2, minute=0),
        },
    }

``schedule.py`` modules must stay import-light (no models, no services): they
are imported while Django settings are still loading.
"""

from importlib import import_module
from importlib.util import find_spec

from config.modules import ALL_MODULES


def build_beat_schedule(installed_apps) -> dict:
    """Merge ``BEAT_SCHEDULE`` from every installed module that declares one."""
    installed = set(installed_apps)
    schedule: dict = {}
    for label in ALL_MODULES:
        if label not in installed:
            continue
        module_name = f"{label}.schedule"
        if find_spec(module_name) is None:
            continue
        entries = getattr(import_module(module_name), "BEAT_SCHEDULE", {})
        duplicates = schedule.keys() & entries.keys()
        if duplicates:
            raise ValueError(f"Duplicate beat entries declared by {label}: {sorted(duplicates)}")
        schedule.update(entries)
    return schedule
