"""The few ARI calls the web side makes: Asterisk's stored recordings (voicemail, call recordings).

Plain HTTP to the local ARI; nothing here changes a live call (rule 2: only the brain does that).
"""

from urllib.parse import quote

import requests
from django.conf import settings


def _url(path: str) -> str:
    return f"{settings.ARI_URL}{path}"


def _auth() -> tuple[str, str]:
    return (settings.ARI_USER, settings.ARI_PASSWORD)


def _name(name: str) -> str:
    return quote(name, safe="")  # calls/<id> is one path segment for ARI


def fetch_recording(name: str) -> bytes | None:
    """The file Asterisk stored under ``name`` (it may sit in a folder, like calls/<id>), or None."""
    response = requests.get(_url(f"/recordings/stored/{_name(name)}/file"), auth=_auth(), timeout=30)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


def delete_recording(name: str) -> None:
    response = requests.delete(_url(f"/recordings/stored/{_name(name)}"), auth=_auth(), timeout=10)
    if response.status_code != 404:
        response.raise_for_status()


def channel_exists(channel_id: str) -> bool:
    response = requests.get(_url(f"/channels/{_name(channel_id)}"), auth=_auth(), timeout=10)
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return True
