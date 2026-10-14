"""Celery application.

Tasks are auto-discovered from every installed app's ``tasks.py``. The broker
and result backend come from Django settings (the ``CELERY_*`` namespace).

The request ID of the HTTP request that queued a task travels with it, so a
log line written inside a worker carries the same ``request_id`` as the API
call that caused it.
"""

import os

from celery import Celery
from celery.signals import before_task_publish, task_postrun, task_prerun

# Default to development settings if nothing else is set (e.g. `celery worker`).
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery(os.environ.get("PROJECT_SLUG", "starter"))

# All Celery configuration keys live in Django settings, prefixed with CELERY_.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Discover tasks.py modules across INSTALLED_APPS.
app.autodiscover_tasks()

REQUEST_ID_HEADER = "x_request_id"


@before_task_publish.connect
def _attach_request_id(headers=None, **_kwargs):
    """Copy the current request ID onto the outgoing task message."""
    from apps.core.context import get_request_id

    request_id = get_request_id()
    if headers is not None and request_id:
        headers[REQUEST_ID_HEADER] = request_id


@task_prerun.connect
def _restore_request_id(task=None, **_kwargs):
    """Make the originating request ID current for the task's lifetime."""
    from apps.core.context import set_request_id

    request_id = getattr(getattr(task, "request", None), REQUEST_ID_HEADER, None)
    set_request_id(request_id or None)


@task_postrun.connect
def _clear_context(**_kwargs):
    """Never leak a request ID or acting user into the next task."""
    from apps.core.context import set_current_user, set_request_id

    set_request_id(None)
    set_current_user(None)


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Trivial task for verifying the worker is wired up correctly."""
    print(f"Request: {self.request!r}")
