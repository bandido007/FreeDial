"""UX measurements the calls app puts on the events stream for other apps (UX_PRINCIPLES.md section 6)."""

import logging
import uuid

import redis

from apps.calls import bus
from apps.core.admin_workspace import admin_form_refused
from contracts import messages as m

log = logging.getLogger(__name__)


def _form_refused(sender, model, fields, **kwargs):
    try:
        bus.publish_ux(
            m.UX_ADMIN_VALIDATION_ERROR, {"model": model, "fields": list(fields)}, str(uuid.uuid4())
        )
    except redis.RedisError as exc:  # measurement must never break an admin page
        log.warning("ux.admin_validation_error not sent: %s", exc)


def connect():
    admin_form_refused.connect(_form_refused, dispatch_uid="calls.ux.admin_form_refused")
