"""ARI, Asterisk's REST interface and event WebSocket, over aiohttp.

:func:`perform` turns a core action into its REST call. ``brain.sim.FakeAri`` offers
the same ``request`` and ``events`` so the brain runs unchanged against it.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

import aiohttp

from brain.core import actions as a


class AriError(Exception):
    """ARI answered with an error status."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class AriLike(Protocol):
    async def request(self, method: str, path: str, *, missing_ok: bool = False, **params: str) -> Any: ...

    def events(self) -> Any: ...


class Ari:
    def __init__(self, *, url: str, user: str, password: str, app: str = "algodial", timeout: float = 5.0):
        self.url = url
        self.app = app
        self._headers = {"Authorization": aiohttp.encode_basic_auth(user, password)}
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "Ari":
        self._session = aiohttp.ClientSession(headers=self._headers, timeout=self._timeout)
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._session:
            await self._session.close()

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("use Ari as an async context manager")
        return self._session

    async def request(self, method: str, path: str, *, missing_ok: bool = False, **params: str) -> Any:
        async with self._http().request(method, self.url + path, params=params) as response:
            if response.status == 404 and missing_ok:
                return None
            if response.status >= 300:
                raise AriError(
                    f"{method} {path} -> {response.status} {await response.text()}", response.status
                )
            return None if response.status == 204 else await response.json()

    async def asterisk_info(self) -> dict[str, Any]:
        return await self.request("GET", "/asterisk/info")

    @asynccontextmanager
    async def events(self) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        """Connect the event WebSocket as our Stasis app; yields the stream of events."""
        url = self.url.replace("http", "ws", 1) + "/events"
        async with self._http().ws_connect(
            url, params={"app": self.app}, timeout=aiohttp.ClientWSTimeout(ws_close=5), heartbeat=20
        ) as ws:

            async def stream() -> AsyncIterator[dict[str, Any]]:
                async for message in ws:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        yield json.loads(message.data)
                    elif message.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                        break

            yield stream()


async def perform(ari: AriLike, action: a.AriAction) -> None:
    """Carry out one ARI action. Hangups and deletes of things already gone are fine (404)."""
    match action:
        case a.Answer(channel=channel):
            await ari.request("POST", f"/channels/{channel}/answer")
        case a.CreateBridge(bridge=bridge, name=name):
            await ari.request("POST", "/bridges", type="mixing", bridgeId=bridge, name=name)
        case a.AddToBridge(bridge=bridge, channel=channel):
            await ari.request("POST", f"/bridges/{bridge}/addChannel", channel=channel)
        case a.RemoveFromBridge(bridge=bridge, channel=channel):
            await ari.request("POST", f"/bridges/{bridge}/removeChannel", channel=channel)
        case a.StartMoh(bridge=bridge, moh_class=moh_class):
            await ari.request("POST", f"/bridges/{bridge}/moh", mohClass=moh_class)
        case a.StopMoh(bridge=bridge):
            await ari.request("DELETE", f"/bridges/{bridge}/moh", missing_ok=True)
        case a.Originate():
            await ari.request(
                "POST",
                "/channels",
                endpoint=action.endpoint,
                app=ari_app(ari),
                appArgs=action.app_args,
                channelId=action.channel,
                originator=action.originator,
                callerId=action.caller_id,
                timeout=str(action.timeout),
            )
        case a.Snoop():
            await ari.request(
                "POST",
                f"/channels/{action.channel}/snoop",
                spy=action.spy,
                whisper=action.whisper,
                app=ari_app(ari),
                appArgs=action.app_args,
                snoopId=action.snoop_id,
            )
        case a.Play(channel=channel, media=media, playback_id=playback_id):
            await ari.request("POST", f"/channels/{channel}/play", media=media, playbackId=playback_id)
        case a.Record():
            await ari.request(
                "POST",
                f"/channels/{action.channel}/record",
                name=action.name,
                format="wav",
                maxDurationSeconds=str(action.max_seconds),
                maxSilenceSeconds=str(action.max_silence_seconds),
                ifExists="overwrite",
                beep="true" if action.beep else "false",
                terminateOn="#",
            )
        case a.Hangup(channel=channel, reason=reason):
            await ari.request("DELETE", f"/channels/{channel}", missing_ok=True, reason=reason)
        case a.DeleteBridge(bridge=bridge):
            await ari.request("DELETE", f"/bridges/{bridge}", missing_ok=True)
        case a.Close():
            pass
        case _:
            raise TypeError(f"not an ARI action: {action!r}")


def ari_app(ari: AriLike) -> str:
    return str(getattr(ari, "app", "algodial"))
