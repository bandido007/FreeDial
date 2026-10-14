"""The catalogue of runtime settings, assembled from every installed app."""

from apps.core.catalogue import SETTING_CHOICE, SETTING_TYPES, SettingSpec
from apps.core.registry import app_modules

MODULE = "settings_catalogue"

_cache: dict[str, tuple[str, SettingSpec]] | None = None


def declared() -> list[tuple[str, SettingSpec]]:
    """``(app_label, spec)`` for every declaration, duplicates included."""
    found = []
    for app_config, module in app_modules(MODULE):
        for spec in getattr(module, "SETTINGS", []) or []:
            found.append((app_config.label, spec))
    return found


def _load() -> dict[str, tuple[str, SettingSpec]]:
    global _cache
    if _cache is None:
        loaded: dict[str, tuple[str, SettingSpec]] = {}
        for label, spec in declared():
            loaded.setdefault(spec.key, (label, spec))
        _cache = loaded
    return _cache


def reset_cache() -> None:
    global _cache
    _cache = None


def specs() -> list[SettingSpec]:
    return [spec for _label, spec in _load().values()]


def get(key: str) -> SettingSpec | None:
    entry = _load().get(key)
    return entry[1] if entry else None


def problems() -> list[str]:
    """Everything wrong with the declared settings."""
    found: list[str] = []
    owners: dict[str, str] = {}
    for label, spec in declared():
        where = f"{spec.key} ({label})"
        if not spec.key or spec.key != spec.key.upper() or " " in spec.key:
            found.append(f"{where}: the key must be UPPER_SNAKE_CASE.")
        if spec.key in owners:
            found.append(f"{where}: already declared by '{owners[spec.key]}'.")
        owners.setdefault(spec.key, label)
        if spec.type not in SETTING_TYPES:
            found.append(f"{where}: unknown type '{spec.type}'.")
        if spec.type == SETTING_CHOICE and not spec.choices:
            found.append(f"{where}: a CHOICE setting needs its choices.")
        if not (spec.name or "").strip() or len((spec.description or "").strip()) < 15:
            found.append(f"{where}: needs a name and a description a person can act on.")
        if not (spec.group or "").strip():
            found.append(f"{where}: needs a group.")
    return found
