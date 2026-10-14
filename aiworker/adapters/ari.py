"""The one thing the worker asks ARI: the connection id of a call's media leg (read-only, rule 2)."""

import asyncio
from urllib.parse import quote

import aiohttp
from django.conf import settings

VARIABLE = "MEDIA_WEBSOCKET_CONNECTION_ID"


async def connection_id(http: aiohttp.ClientSession, leg: str, *, wait: float = 3.0) -> str | None:
    """The id to connect to ``/media/<id>`` with. The brain may still be creating the leg: retry a little."""
    url = f"{settings.ARI_URL}/channels/{quote(leg, safe='')}/variable"
    auth = aiohttp.BasicAuth(settings.ARI_USER, settings.ARI_PASSWORD)
    deadline = asyncio.get_running_loop().time() + wait
    while True:
        async with http.get(url, params={"variable": VARIABLE}, auth=auth) as response:
            if response.status == 200:
                return str((await response.json()).get("value") or "") or None
            if response.status != 404:
                response.raise_for_status()
        if asyncio.get_running_loop().time() > deadline:
            return None
        await asyncio.sleep(0.1)
