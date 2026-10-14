"""Reading and changing runtime settings.

:func:`override_for` is what :func:`apps.core.hooks.policy` calls on every
lookup, so it must be cheap: all saved values are held in the cache as one
dictionary, rebuilt when any of them changes.
"""

import logging

from django.conf import settings as django_settings
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator, validate_email

from apps.configuration import catalogue
from apps.configuration.models import SystemSetting
from apps.core.catalogue import (
    SETTING_BOOLEAN,
    SETTING_CHOICE,
    SETTING_EMAIL,
    SETTING_INTEGER,
    SETTING_LIST,
    SETTING_URL,
    SettingSpec,
)
from apps.core.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

UNKNOWN_SETTING = 9200
INVALID_VALUE = 9201

CACHE_KEY = "configuration:overrides"
CACHE_SECONDS = 300
_ABSENT = object()


# ── Reading ──────────────────────────────────────────────────────────────────
def overrides() -> dict:
    """Every saved value, keyed by setting."""
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached
    try:
        saved = dict(SystemSetting.objects.values_list("key", "value"))
    except Exception:  # noqa: BLE001 — table not migrated yet, database down
        return {}
    cache.set(CACHE_KEY, saved, timeout=CACHE_SECONDS)
    return saved


def forget() -> None:
    cache.delete(CACHE_KEY)


def override_for(key: str):
    """The saved value of ``key``, or ``None`` when nobody changed it."""
    return overrides().get(key)


def default_for(spec: SettingSpec):
    """What the setting is when nobody changed it: the Django setting of the
    same name, else the declared default."""
    configured = getattr(django_settings, spec.key, _ABSENT)
    return spec.default if configured is _ABSENT else configured


def value_of(key: str):
    """The effective value of a declared setting."""
    spec = _spec(key)
    saved = overrides().get(key, _ABSENT)
    return default_for(spec) if saved is _ABSENT or saved is None else saved


def describe(spec: SettingSpec, saved: dict | None = None) -> dict:
    saved = overrides() if saved is None else saved
    default = default_for(spec)
    changed = spec.key in saved and saved[spec.key] is not None
    return {
        "key": spec.key,
        "group": spec.group,
        "name": spec.name,
        "description": spec.description,
        "type": spec.type,
        "value": saved[spec.key] if changed else default,
        "default_value": default,
        "is_changed": changed,
        "choices": list(spec.choices),
        "minimum": spec.minimum,
        "maximum": spec.maximum,
        "public": spec.public,
    }


def all_settings(*, public_only: bool = False) -> list[dict]:
    saved = overrides()
    rows = [describe(spec, saved) for spec in catalogue.specs() if spec.public or not public_only]
    return sorted(rows, key=lambda row: (row["group"], row["name"]))


def public_values() -> dict:
    """``{key: value}`` of the settings readable without signing in."""
    return {row["key"]: row["value"] for row in all_settings(public_only=True)}


# ── Changing ─────────────────────────────────────────────────────────────────
def _spec(key: str) -> SettingSpec:
    spec = catalogue.get(key)
    if spec is None:
        raise NotFoundError(f"Unknown setting '{key}'", code=UNKNOWN_SETTING)
    return spec


def _refuse(spec: SettingSpec, reason: str):
    raise ValidationError(f"{spec.name}: {reason}", code=INVALID_VALUE)


def clean(spec: SettingSpec, value):
    """``value`` coerced to the setting's type; raises when it does not fit."""
    if spec.type == SETTING_BOOLEAN:
        if isinstance(value, bool):
            return value
        if str(value).strip().lower() in ("true", "1", "yes", "on"):
            return True
        if str(value).strip().lower() in ("false", "0", "no", "off"):
            return False
        _refuse(spec, "choose yes or no.")

    if spec.type == SETTING_INTEGER:
        if isinstance(value, bool):
            _refuse(spec, "enter a whole number.")
        try:
            number = int(value)
        except (TypeError, ValueError):
            _refuse(spec, "enter a whole number.")
        if spec.minimum is not None and number < spec.minimum:
            _refuse(spec, f"the smallest value allowed is {spec.minimum}.")
        if spec.maximum is not None and number > spec.maximum:
            _refuse(spec, f"the largest value allowed is {spec.maximum}.")
        return number

    if spec.type == SETTING_LIST:
        if isinstance(value, str):
            value = [part for part in (item.strip() for item in value.split(",")) if part]
        if not isinstance(value, (list, tuple)):
            _refuse(spec, "enter a list of values.")
        items = list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
        if spec.choices:
            unknown = [item for item in items if item not in spec.choices]
            if unknown:
                _refuse(spec, f"not allowed: {', '.join(unknown)}.")
        return items

    text = "" if value is None else str(value).strip()
    if spec.type == SETTING_CHOICE:
        if text not in spec.choices:
            _refuse(spec, f"choose one of: {', '.join(spec.choices)}.")
        return text
    if spec.maximum is not None and len(text) > spec.maximum:
        _refuse(spec, f"keep it under {spec.maximum} characters.")
    if text and spec.type == SETTING_EMAIL:
        try:
            validate_email(text)
        except DjangoValidationError:
            _refuse(spec, "enter a valid e-mail address.")
    if text and spec.type == SETTING_URL:
        try:
            URLValidator(schemes=["http", "https"])(text)
        except DjangoValidationError:
            _refuse(spec, "enter a full web address, starting with https://.")
    return text


def set_value(*, key: str, value, actor=None) -> dict:
    """Save a new value for ``key``. The change lands in the audit trail."""
    spec = _spec(key)
    cleaned = clean(spec, value)
    row = SystemSetting.all_objects.filter(key=key).first() or SystemSetting(key=key)
    row.value = cleaned
    row.deleted = False
    row.deleted_at = None
    row.active = True
    row.save()
    forget()
    logger.info("[set_value] %s changed by %s", key, getattr(actor, "pk", None))
    return describe(spec)


def reset_value(*, key: str, actor=None) -> dict:
    """Go back to the default: the saved value is removed."""
    spec = _spec(key)
    for row in SystemSetting.objects.filter(key=key):
        row.delete()  # soft delete: the trail keeps what it was
    forget()
    logger.info("[reset_value] %s reset by %s", key, getattr(actor, "pk", None))
    return describe(spec)
