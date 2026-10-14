"""Status-code catalogue and message resolution.

Both :class:`~apps.core.graphql.response.Response` and
:class:`~apps.core.graphql.pagination.Page` build the same envelope, so the
status lookup and the translated message selection live here once.

The catalogue is **assembled from every installed app**: each app ships its own
``status_codes.json`` next to its code::

    [
      {"code": 10001, "status": false,
       "messageEn": "This invoice is already paid.",
       "messageSw": "Ankara hii imeshalipwa."}
    ]

Number ranges
-------------
=============  ==========================================
8000 – 8999    sign-in and tokens (foundation)
9000 – 9999    foundation apps
10000 and up   your business apps
=============  ==========================================

A system check (``apps.core.checks``) fails the build when two apps claim the
same code, a translation is missing, or a business app uses a foundation
number.

Language is chosen from the ``Content-Language`` request header, matched
against ``API_LANGUAGES`` (first entry is the fallback).
"""

import json
from functools import lru_cache

from django.conf import settings

from apps.core.registry import app_files
from apps.core.utils import Utils

STATUS_FILE = "status_codes.json"
MISSING_FIELDS_CODE = 9005


def message_key(language: str) -> str:
    """``"sw"`` → ``"messageSw"``."""
    return "message" + language[:2].capitalize()


def languages() -> list[str]:
    configured = [lang.lower()[:2] for lang in getattr(settings, "API_LANGUAGES", None) or [] if lang]
    return configured or ["en"]


def load_catalogue() -> list[tuple[str, dict]]:
    """Every declared entry as ``(app_label, entry)`` — duplicates included."""
    entries: list[tuple[str, dict]] = []
    for app_config, path in app_files(STATUS_FILE):
        with open(path, encoding="utf-8") as fh:
            for entry in json.load(fh):
                entries.append((app_config.label, entry))
    return entries


@lru_cache(maxsize=1)
def _status_map() -> dict:
    merged: dict = {}
    for _label, entry in load_catalogue():
        # First declaration wins; the system check reports the collision.
        merged.setdefault(entry["code"], entry)
    return merged


def reset_cache() -> None:
    """Forget the assembled catalogue (tests that install apps on the fly)."""
    _status_map.cache_clear()


def get_status(code: int) -> dict:
    return _status_map().get(
        code,
        {
            "code": code,
            "status": False,
            "messageEn": "Unknown status code",
        },
    )


def _language(info) -> str:
    supported = languages()
    try:
        if info is not None and hasattr(info, "context") and hasattr(info.context, "headers"):
            requested = (info.context.headers.get("Content-Language") or "").lower()[:2]
            if requested in supported:
                return requested
    except Exception:  # noqa: BLE001
        pass
    return supported[0]


def resolve_message(code: int, info=None, custom_message: str | None = None) -> str:
    """Return the translated message for ``code`` (or ``custom_message``).

    For the generic "required argument missing" code (9005) the names of the
    fields that were actually missing are appended, as recorded by
    :meth:`Utils.empty_and_null_checker`.
    """
    if custom_message:
        return custom_message

    status_info = get_status(code)
    fallback = languages()[0]
    message = (
        status_info.get(message_key(_language(info)))
        or status_info.get(message_key(fallback))
        or status_info.get("messageEn")
        or ""
    )

    if code == MISSING_FIELDS_CODE:
        missing = Utils.get_last_missing_fields()
        if missing:
            return f"Missing required field(s): {', '.join(missing)}"

    return message
