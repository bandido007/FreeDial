"""Model-change auditing — one place, every model.

Instead of every service remembering to write an audit row, three receivers are
connected globally (``sender=None``) and decide per model class whether it is
audited (see :func:`should_audit` and :mod:`apps.audit.constants`).

Flow per save: ``pre_save`` snapshots the row's current database values onto
the instance (one indexed primary-key query), ``post_save`` diffs old against
new and hands the classified change to ``services.record_audit``.
``post_delete`` records the final snapshot of hard-deleted rows. Soft deletes
go through ``save()`` and are classified out of the ``deleted`` flag flip.

Known, accepted limitation: ``queryset.update()`` / ``bulk_create`` bypass
model signals (that is how Django works) — a bulk writer that must be audited
uses :func:`apps.audit.services.audited_update` instead.

Receivers never raise: a failure to audit must not break the write it observes.
"""

import datetime
import decimal
import logging
import uuid
from functools import cache

from django.conf import settings
from django.db.models.signals import post_delete, post_save, pre_save

from apps.audit.constants import IGNORED_DIFF_FIELDS, SENSITIVE_MASK, names_a_secret
from apps.audit.enums import AuditActionChoices

logger = logging.getLogger(__name__)

PRIOR_ATTR = "_audit_prior_state"
MAX_STR = 500
EMPTY: tuple = (None, "", [], {})


@cache
def should_audit(model) -> bool:
    from apps.core.models import BaseModel

    if getattr(model, "AUDIT_EXCLUDE", False):
        return False
    label = model._meta.label_lower
    extra = {name.lower() for name in getattr(settings, "AUDIT_EXTRA_MODELS", None) or []}
    if label in extra:
        return True
    app_config = getattr(model._meta, "app_config", None)
    if app_config is None or not app_config.name.startswith("apps."):
        return False
    if app_config.label == "audit" and not issubclass(model, BaseModel):
        return False  # the logs themselves
    if getattr(model, "AUDIT_INCLUDE", False):
        return True
    return issubclass(model, BaseModel)


@cache
def diffable_fields(model) -> tuple[str, ...]:
    """Concrete column attnames worth diffing (foreign keys by ``*_id``)."""
    ignored = IGNORED_DIFF_FIELDS | set(getattr(model, "AUDIT_IGNORE_FIELDS", ()) or ())
    return tuple(f.attname for f in model._meta.concrete_fields if f.attname not in ignored)


def is_sensitive(model, field_name: str) -> bool:
    if field_name in (getattr(model, "AUDIT_MASK_FIELDS", ()) or ()):
        return True
    return names_a_secret(field_name)


def json_safe(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= MAX_STR else value[:MAX_STR] + "…"
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in list(value)[:50]]
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in list(value.items())[:50]}
    return json_safe(str(value))


def entry(model, field_name: str, old, new) -> dict:
    if is_sensitive(model, field_name):
        return {
            "from": SENSITIVE_MASK if old not in (None, "") else old,
            "to": SENSITIVE_MASK if new not in (None, "") else new,
        }
    return {"from": old, "to": new}


def classify_update(changes: dict) -> str:
    """Read the intent out of a diff on the lifecycle flags."""
    if "deleted" in changes:
        return (
            AuditActionChoices.SoftDeleted.value
            if changes["deleted"]["to"]
            else AuditActionChoices.Restored.value
        )
    lifecycle = set(changes) - {"deactivated_at", "deleted_at"}
    for flag in ("active", "is_active"):
        if lifecycle == {flag}:
            return (
                AuditActionChoices.Deactivated.value
                if not changes[flag]["to"]
                else AuditActionChoices.Activated.value
            )
    return AuditActionChoices.Updated.value


def diff(model, prior: dict, current: dict) -> dict:
    """``{field: {from, to}}`` for every field whose value differs."""
    changes = {}
    for name in diffable_fields(model):
        if name not in current:
            continue
        old, new = json_safe(prior.get(name)), json_safe(current.get(name))
        if old != new:
            changes[name] = entry(model, name, old, new)
    return changes


# ── Receivers ────────────────────────────────────────────────────────────────
def _capture_prior_state(sender, instance, raw=False, **kwargs):
    if raw or not should_audit(sender) or instance.pk is None:
        return
    try:
        prior = sender._base_manager.filter(pk=instance.pk).values(*diffable_fields(sender)).first()
        setattr(instance, PRIOR_ATTR, prior)
    except Exception:  # noqa: BLE001
        logger.debug("[audit] could not snapshot %s pk=%s", sender.__name__, instance.pk)


def _log_save(sender, instance, created=False, raw=False, **kwargs):
    if raw or not should_audit(sender):
        return
    try:
        from apps.audit import services

        if created:
            changes = {}
            for name in diffable_fields(sender):
                new = json_safe(getattr(instance, name, None))
                if new not in EMPTY:
                    changes[name] = entry(sender, name, None, new)
            services.record_audit(instance=instance, action=AuditActionChoices.Created.value, changes=changes)
            return

        prior = instance.__dict__.pop(PRIOR_ATTR, None)
        if prior is None:  # row existed before this process saw it change
            return
        current = {name: getattr(instance, name, None) for name in diffable_fields(sender)}
        changes = diff(sender, prior, current)
        if not changes:
            return
        services.record_audit(instance=instance, action=classify_update(changes), changes=changes)
    except Exception:  # noqa: BLE001 — never break the write being audited
        logger.warning("[audit] failed to log save of %s", sender.__name__, exc_info=True)
    finally:
        instance.__dict__.pop(PRIOR_ATTR, None)


def _log_delete(sender, instance, **kwargs):
    if not should_audit(sender):
        return
    try:
        from apps.audit import services

        changes = {}
        for name in diffable_fields(sender):
            old = json_safe(getattr(instance, name, None))
            if old not in EMPTY:
                changes[name] = entry(sender, name, old, None)
        services.record_audit(instance=instance, action=AuditActionChoices.Deleted.value, changes=changes)
    except Exception:  # noqa: BLE001
        logger.warning("[audit] failed to log delete of %s", sender.__name__, exc_info=True)


def connect_signals() -> None:
    pre_save.connect(_capture_prior_state, dispatch_uid="audit_capture_prior_state")
    post_save.connect(_log_save, dispatch_uid="audit_log_save")
    post_delete.connect(_log_delete, dispatch_uid="audit_log_delete")
