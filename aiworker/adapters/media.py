"""One call's audio: the WebSocket to Asterisk's ``/media/<connection id>`` (chan_websocket).

The brain opened the leg with ARI externalMedia (transport websocket, Asterisk as the server,
format slin24, control messages as JSON). Binary messages are audio both ways; text messages
are control: MEDIA_START, MEDIA_MARK_PROCESSED, MEDIA_XOFF / MEDIA_XON from Asterisk, and our
MARK_MEDIA (tell me when what I sent so far has played) and FLUSH_MEDIA (drop what is queued).
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
from django.conf import settings

log = logging.getLogger("aiworker")


def media_url(connection_id: str) -> str:
    """ws://<Asterisk's HTTP server>/media/<id>, next to ARI (``ARI_URL`` without /ari)."""
    base = settings.ARI_URL.removesuffix("/ari")
    return base.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + f"/media/{connection_id}"


class AsteriskMedia:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse):
        self.ws = ws

    @classmethod
    async def connect(cls, http: aiohttp.ClientSession, connection_id: str) -> "AsteriskMedia":
        ws = await http.ws_connect(
            media_url(connection_id), protocols=("media",), heartbeat=None, max_msg_size=0
        )
        return cls(ws)

    async def events(self) -> AsyncIterator[tuple[str, Any]]:
        async for message in self.ws:
            if message.type == aiohttp.WSMsgType.BINARY:
                yield "audio", message.data
            elif message.type == aiohttp.WSMsgType.TEXT:
                try:
                    control = json.loads(message.data)
                except json.JSONDecodeError:
                    control = {"event": str(message.data).split(" ", 1)[0]}
                event = control.get("event")
                if event == "MEDIA_START":
                    yield "start", control
                elif event == "MEDIA_MARK_PROCESSED":
                    yield "mark", control.get("correlation_id", "")
                elif event in ("MEDIA_XOFF", "MEDIA_XON", "ERROR"):
                    log.info("media %s: %s", event, control)
            elif message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break
        yield "closed", None

    async def send_audio(self, pcm: bytes) -> None:
        if not self.ws.closed:
            await self.ws.send_bytes(pcm)

    async def mark(self, name: str) -> None:
        await self._command("MARK_MEDIA", correlation_id=name)

    async def flush(self) -> None:
        await self._command("FLUSH_MEDIA")

    async def _command(self, command: str, **fields: Any) -> None:
        if not self.ws.closed:
            await self.ws.send_str(json.dumps({"command": command, **fields}))

    async def wait_closed(self, timeout: float) -> bool:
        """Wait for Asterisk to end the leg (the brain hangs it up). True if it did within ``timeout``."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not self.ws.closed:
            left = deadline - loop.time()
            if left <= 0:
                return False
            try:
                message = await self.ws.receive(timeout=left)
            except TimeoutError:
                return False
            if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                return True
        return True

    async def close(self) -> None:
        await self.ws.close()
