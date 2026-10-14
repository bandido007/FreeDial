"""A small fake Asterisk that speaks the brain's ARI: channels, bridges, music, and their events.

Its events copy what Asterisk 22.11 really sends, as recorded on the lab:

* An agent leg the brain originates is ``AppDial2`` while it rings, and enters Stasis
  (``StasisStart`` with the originate's appArgs) only when answered.
* A rejected leg ends with ``ChannelDestroyed`` cause 17 (SIP 486), a leg that rang out
  with cause 19, a leg the brain hung up with cause 16.
* Ringing an endpoint with no registered phone fails the originate itself:
  ``500 Allocation failed``, and no channel exists.
* A customer who hangs up sends ``StasisEnd`` only: after leaving the app the channel is
  no longer the app's, so no ``ChannelDestroyed`` reaches it.
* With no app connected, ``Stasis()`` fails and the dialplan falls back (rule 5): the
  customer's channel goes to ``Queue``.
"""

import asyncio
import itertools
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from brain.adapters.ari import AriError

ASTERISK_ID = "fa:ke:00:00:00:01"


def ari_time(epoch: float) -> str:
    moment = datetime.fromtimestamp(epoch).astimezone()
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}" + moment.strftime("%z")


class FakeAri:
    def __init__(self, *, app: str = "algodial", clock: Callable[[], float] = time.time):
        self.app = app
        self.clock = clock
        self.channels: dict[str, dict[str, Any]] = {}
        self.bridges: dict[str, dict[str, Any]] = {}
        self.moh: set[str] = set()  # bridges playing music on hold
        self.registered: set[str] = set()  # endpoints with a phone online, e.g. PJSIP/1001
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.failures: dict[tuple[str, str], int] = {}  # (method, path) -> status to answer once
        self.fallbacks: list[str] = []  # customers sent to the dialplan's Queue() (no brain)
        self.ringing: asyncio.Queue[str] = asyncio.Queue()
        self._streams: list[asyncio.Queue[dict[str, Any] | None]] = []
        self._numbers = itertools.count(1)
        self._timers: dict[str, asyncio.TimerHandle] = {}

    # ── the brain's side: REST and the event stream ───────────────────────
    @property
    def app_connected(self) -> bool:
        return bool(self._streams)

    @asynccontextmanager
    async def events(self) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._streams.append(queue)

        async def stream() -> AsyncIterator[dict[str, Any]]:
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event

        try:
            yield stream()
        finally:
            if queue in self._streams:
                self._streams.remove(queue)

    def disconnect(self) -> None:
        """The WebSocket drops: every connected stream ends."""
        for queue in self._streams:
            queue.put_nowait(None)
        self._streams.clear()

    async def request(self, method: str, path: str, *, missing_ok: bool = False, **params: str) -> Any:
        self.requests.append((method, path, dict(params)))
        await asyncio.sleep(0)  # a real request yields to the loop
        status = self.failures.pop((method, path), None)
        if status is not None:
            raise AriError(f"{method} {path} -> {status} (injected)", status)
        try:
            return self._route(method, path.strip("/").split("/"), params)
        except AriError as exc:
            if exc.status == 404 and missing_ok:
                return None
            raise

    def _route(self, method: str, parts: list[str], params: dict[str, str]) -> Any:
        match method, parts:
            case "GET", ["asterisk", "info"]:
                return {"system": {"version": "22.11.0 (fake)"}}
            case "GET", ["channels"]:
                return [self._public(channel) for channel in self.channels.values()]
            case "GET", ["bridges"]:
                return [dict(bridge, channels=list(bridge["channels"])) for bridge in self.bridges.values()]
            case "POST", ["channels"]:
                return self._originate(params)
            case "POST", ["channels", cid, "answer"]:
                self._channel(cid)["state"] = "Up"
                return None
            case "DELETE", ["channels", cid]:
                self._channel(cid)
                self._destroy(cid, cause=16, cause_txt="Normal Clearing")
                return None
            case "POST", ["bridges"]:
                bid = params.get("bridgeId") or str(uuid.uuid4())
                self.bridges[bid] = {
                    "id": bid,
                    "technology": "simple_bridge",
                    "bridge_type": params.get("type", "mixing"),
                    "bridge_class": "stasis",
                    "creator": "Stasis",
                    "name": params.get("name", ""),
                    "channels": [],
                    "creationtime": ari_time(self.clock()),
                    "video_mode": "talker",
                }
                return dict(self.bridges[bid])
            case "POST", ["bridges", bid, "addChannel"]:
                bridge = self._bridge(bid)
                cid = params["channel"]
                if cid not in self.channels:
                    raise AriError("Channel not found", 422)
                self._leave_bridges(cid)
                bridge["channels"].append(cid)
                self._emit("ChannelEnteredBridge", self.channels[cid], bridge=dict(bridge))
                return None
            case "POST", ["bridges", bid, "removeChannel"]:
                bridge = self._bridge(bid)
                if params["channel"] not in bridge["channels"]:
                    raise AriError("Channel not in this bridge", 422)
                self._leave_bridges(params["channel"])
                return None
            case "POST", ["bridges", bid, "moh"]:
                self._bridge(bid)
                self.moh.add(bid)
                return None
            case "DELETE", ["bridges", bid, "moh"]:
                self._bridge(bid)
                self.moh.discard(bid)
                return None
            case "DELETE", ["bridges", bid]:
                bridge = self._bridge(bid)
                for cid in list(bridge["channels"]):
                    self._leave_bridges(cid)
                del self.bridges[bid]
                self.moh.discard(bid)
                return None
        raise AriError(f"{method} /{'/'.join(parts)} is not in the fake", 501)

    # ── the phones' side: what customers and agents do ────────────────────
    def call_in(
        self, entry: str = "800", caller: str = "lab-customer", *, args: list[str] | None = None
    ) -> str:
        """A customer dials ``entry``. With the default args the dialplan of extension 800 is used."""
        number = next(self._numbers)
        cid = f"{1_791_940_000 + number}.{number}"
        if args is None:
            args = [
                "inbound",
                entry,
                caller,
                cid,
            ]  # Stasis(algodial,inbound,${EXTEN},${CHANNEL(endpoint)},${CHANNEL(linkedid)})
        channel = self._new_channel(cid, f"PJSIP/{caller}", state="Ring", caller=(caller, caller))
        if not self.app_connected:  # Stasis() fails: STASISSTATUS=FAILED, the dialplan's Queue() takes it
            channel["dialplan"].update(app_name="Queue", app_data="support")
            self.fallbacks.append(cid)
            return cid
        channel["dialplan"].update(context="lab", exten=entry, priority=2)
        self._enter_stasis(cid, args)
        return cid

    def answer(self, cid: str) -> None:
        """The agent picks up the ringing phone."""
        channel = self._channel(cid)
        self._cancel_timer(cid)
        channel["state"] = "Up"
        self._enter_stasis(cid, channel["_app_args"].split(","))

    def reject(self, cid: str, cause: int = 17, cause_txt: str = "User busy") -> None:
        self._channel(cid)
        self._destroy(cid, cause=cause, cause_txt=cause_txt)

    def hangup(self, cid: str) -> None:
        """The phone hangs up (SIP BYE or CANCEL from the phone's side)."""
        self._channel(cid)
        self._destroy(cid, cause=16, cause_txt="Normal Clearing")

    async def next_ringing(self, timeout: float = 3.0) -> str:
        return await asyncio.wait_for(self.ringing.get(), timeout)

    # ── inspection ────────────────────────────────────────────────────────
    def bridge_of(self, cid: str) -> dict[str, Any] | None:
        return next((bridge for bridge in self.bridges.values() if cid in bridge["channels"]), None)

    def originates(self) -> list[dict[str, str]]:
        return [params for method, path, params in self.requests if (method, path) == ("POST", "/channels")]

    # ── internals ─────────────────────────────────────────────────────────
    def _new_channel(self, cid: str, endpoint: str, *, state: str, caller: tuple[str, str]) -> dict[str, Any]:
        if cid in self.channels:
            raise AriError(f"channel {cid} exists", 409)
        channel = {
            "id": cid,
            "name": f"{endpoint}-{next(self._numbers):08x}",
            "state": state,
            "protocol_id": str(uuid.uuid4()),
            "caller": {"name": caller[0], "number": caller[1]},
            "connected": {"name": "", "number": ""},
            "accountcode": "",
            "dialplan": {"context": "lab", "exten": "s", "priority": 1, "app_name": "", "app_data": ""},
            "creationtime": ari_time(self.clock()),
            "language": "en",
            "_subscribed": False,  # does the app get this channel's events?
            "_in_stasis": False,
        }
        self.channels[cid] = channel
        return channel

    def _originate(self, params: dict[str, str]) -> dict[str, Any]:
        endpoint = params["endpoint"]
        if endpoint not in self.registered:
            raise AriError('{"error": "Allocation failed"}', 500)
        cid = params.get("channelId") or str(uuid.uuid4())
        name, _, number = params.get("callerId", "").partition("<")
        channel = self._new_channel(
            cid, endpoint, state="Down", caller=(endpoint.split("/")[-1], endpoint.split("/")[-1])
        )
        channel["connected"] = {"name": name.strip().strip('"'), "number": number.rstrip(">")}
        channel["dialplan"].update(app_name="AppDial2", app_data="(Outgoing Line)")
        channel.update(
            _subscribed=True, _app_args=params.get("appArgs", ""), _originator=params.get("originator")
        )
        channel["state"] = "Ringing"
        timeout = float(params.get("timeout") or 30)
        if timeout > 0:
            self._timers[cid] = asyncio.get_running_loop().call_later(timeout, self._rang_out, cid)
        self.ringing.put_nowait(cid)
        return self._public(channel)

    def _rang_out(self, cid: str) -> None:
        self._timers.pop(cid, None)
        if cid in self.channels and self.channels[cid]["state"] == "Ringing":
            self._destroy(cid, cause=19, cause_txt="No answer")

    def _enter_stasis(self, cid: str, args: list[str]) -> None:
        channel = self.channels[cid]
        channel["dialplan"].update(app_name="Stasis", app_data=",".join([self.app, *args]))
        channel["_subscribed"] = channel["_in_stasis"] = True
        self._emit("StasisStart", channel, args=list(args))

    def _destroy(self, cid: str, *, cause: int, cause_txt: str) -> None:
        channel = self.channels[cid]
        self._cancel_timer(cid)
        self._emit("ChannelHangupRequest", channel, cause=cause)
        self._leave_bridges(cid)
        if channel["_in_stasis"]:
            channel["_in_stasis"] = False
            self._emit("StasisEnd", channel)
            if not channel.get("_app_args"):
                channel["_subscribed"] = False  # a customer leaves the app; the dialplan hangs it up
        self._emit("ChannelDestroyed", channel, cause=cause, cause_txt=cause_txt)
        del self.channels[cid]

    def _leave_bridges(self, cid: str) -> None:
        for bridge in self.bridges.values():
            if cid in bridge["channels"]:
                bridge["channels"].remove(cid)
                self._emit("ChannelLeftBridge", self.channels[cid], bridge=dict(bridge))

    def _cancel_timer(self, cid: str) -> None:
        handle = self._timers.pop(cid, None)
        if handle is not None:
            handle.cancel()

    def _channel(self, cid: str) -> dict[str, Any]:
        if cid not in self.channels:
            raise AriError("Channel not found", 404)
        return self.channels[cid]

    def _bridge(self, bid: str) -> dict[str, Any]:
        if bid not in self.bridges:
            raise AriError("Bridge not found", 404)
        return self.bridges[bid]

    def _public(self, channel: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in channel.items() if not key.startswith("_")} | {
            "dialplan": dict(channel["dialplan"])
        }

    def _emit(self, kind: str, channel: dict[str, Any], **extra: Any) -> None:
        if not channel["_subscribed"]:
            return
        event = {
            "type": kind,
            "timestamp": ari_time(self.clock()),
            **extra,
            "channel": self._public(channel),
            "asterisk_id": ASTERISK_ID,
            "application": self.app,
        }
        for queue in self._streams:
            queue.put_nowait(event)
