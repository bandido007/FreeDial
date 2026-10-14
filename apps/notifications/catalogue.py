"""Notification categories, declared by the app that sends them.

A *category* names one kind of message ("password reset", "invoice approved").
It drives three things: which template renders the message, whether a person
may mute it, and how the delivery log is filtered.

Each app declares its categories in its own ``notifications.py``::

    # apps/invoices/notifications.py
    from apps.notifications.catalogue import Category, Template

    INVOICE_APPROVED = "INVOICE_APPROVED"

    CATEGORIES = [
        Category(
            code=INVOICE_APPROVED,
            name="Invoice approved",
            description="Sent to the requester when their invoice is approved.",
            templates=[
                Template("EMAIL", subject="Invoice {number} approved",
                         body="Dear {full_name}, invoice {number} was approved."),
                Template("SMS", body="Invoice {number} was approved."),
            ],
        ),
    ]

Categories are stored as plain strings, so adding one never needs a database
migration. ``manage.py seed`` installs the declared templates when no template
exists yet for that category and channel; copy an administrator has edited is
never overwritten.

``mandatory`` categories (security and account notices) cannot be muted.
``sensitive`` categories carry a secret (a code, a tokenised link): the stored
row is scrubbed after sending and the message cannot be replayed from the log.
"""

from dataclasses import dataclass, field

from apps.core.registry import app_modules

MODULE = "notifications"
MAX_CODE_LENGTH = 60


@dataclass(frozen=True)
class Template:
    """Default copy for one channel. ``{tokens}`` are filled from the variables."""

    channel: str
    body: str = ""
    subject: str = ""
    name: str = ""


@dataclass(frozen=True)
class Category:
    code: str
    name: str
    description: str = ""
    mandatory: bool = False
    sensitive: bool = False
    templates: list[Template] = field(default_factory=list)


_cache: dict[str, tuple[str, Category]] | None = None


def _load() -> dict[str, tuple[str, Category]]:
    global _cache
    if _cache is None:
        found: dict[str, tuple[str, Category]] = {}
        for app_config, module in app_modules(MODULE):
            for category in getattr(module, "CATEGORIES", []) or []:
                found.setdefault(category.code, (app_config.label, category))
        _cache = found
    return _cache


def reset_cache() -> None:
    global _cache
    _cache = None


def categories() -> list[Category]:
    return [category for _label, category in _load().values()]


def declared() -> list[tuple[str, Category]]:
    """``(app_label, category)`` for every declaration, duplicates included."""
    found = []
    for app_config, module in app_modules(MODULE):
        for category in getattr(module, "CATEGORIES", []) or []:
            found.append((app_config.label, category))
    return found


def get(code: str | None) -> Category | None:
    entry = _load().get(code or "")
    return entry[1] if entry else None


def is_known(code: str | None) -> bool:
    return bool(code) and code in _load()


def is_mandatory(code: str | None) -> bool:
    category = get(code)
    return bool(category and category.mandatory)


def is_sensitive(code: str | None) -> bool:
    category = get(code)
    return bool(category and category.sensitive)


def problems() -> list[str]:
    """Everything wrong with the declared categories."""
    from apps.notifications.enums import NotificationChannelChoices

    channels = set(NotificationChannelChoices.values())
    found: list[str] = []
    owners: dict[str, str] = {}
    for label, category in declared():
        where = f"{category.code} ({label})"
        if not category.code or category.code != category.code.upper() or " " in category.code:
            found.append(f"{where}: the code must be UPPER_SNAKE_CASE.")
        if len(category.code) > MAX_CODE_LENGTH:
            found.append(f"{where}: the code is longer than {MAX_CODE_LENGTH} characters.")
        if category.code in owners:
            found.append(f"{where}: already declared by '{owners[category.code]}'.")
        owners.setdefault(category.code, label)
        if not (category.name or "").strip():
            found.append(f"{where}: the name is missing.")
        seen_channels = set()
        for template in category.templates:
            if template.channel not in channels:
                found.append(f"{where}: unknown channel '{template.channel}'.")
            if template.channel in seen_channels:
                found.append(f"{where}: two templates for channel '{template.channel}'.")
            seen_channels.add(template.channel)
    return found
