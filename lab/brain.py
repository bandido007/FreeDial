"""AlgoDial brain: runs every call that enters the algodial Stasis app.

A call to 800 goes like this:
  caller enters Stasis (args: inbound, 800, <caller ext>)
    -> answer, put the caller in a new mixing bridge with hold music
    -> ring the other extension into the app (args: agent, <ext>)
  agent answers
    -> stop the music, add the agent to the bridge
  either side leaves
    -> hang up the other side and delete the bridge

Run `source ari.env` first; ARI and AUTH come from there.
"""
import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone

import aiohttp

ARI = os.environ.get("ARI", "http://127.0.0.1:8091/ari")
AUTH = os.environ.get("AUTH", "algodial:ChangeMeARI")
APP = "algodial"

# Who rings when an extension dials 800.
AGENT_FOR = {"1001": "1002", "1002": "1001"}
RING_TIMEOUT = 30
BRIDGE_PREFIX = "call-"


def log(msg):
    print(f"{time.strftime('%H:%M:%S')}  {msg}", flush=True)


class Call:
    def __init__(self, caller, caller_ext, agent_ext):
        self.caller = caller
        self.caller_ext = caller_ext
        self.agent_ext = agent_ext
        self.agent = None
        self.bridge = None
        self.ended = False

    def __str__(self):
        return f"[{self.caller_ext}->{self.agent_ext}]"


class Brain:
    def __init__(self, http):
        self.http = http
        self.calls = {}  # channel id -> Call, for both legs

    async def ari(self, method, path, missing_ok=False, **params):
        async with self.http.request(method, ARI + path, params=params) as r:
            if r.status == 404 and missing_ok:
                return None
            if r.status >= 300:
                raise RuntimeError(f"{method} {path} -> {r.status} {await r.text()}")
            return await r.json() if r.status != 204 else None

    async def handle(self, event):
        kind = event["type"]
        channel = event.get("channel", {})
        if kind == "StasisStart":
            args = event.get("args", [])
            log(f"StasisStart  {channel.get('name')}  args={args}")
            if args[:1] == ["inbound"]:
                await self.on_inbound(channel, args)
            elif args[:1] == ["agent"]:
                await self.on_agent_answer(channel)
            else:
                # Every StasisStart must end in a decision, or the caller hears ringing forever.
                log(f"unknown args {args} on {channel.get('name')}, hanging up")
                await self.delete(f"/channels/{channel['id']}")
        elif kind in ("StasisEnd", "ChannelDestroyed"):
            call = self.calls.get(channel.get("id"))
            if call:
                await self.end(call, f"{kind} on {channel.get('name')}")

    async def on_inbound(self, channel, args):
        caller_ext = args[2] if len(args) > 2 else ""
        agent_ext = AGENT_FOR.get(caller_ext)
        if not agent_ext:
            log(f"no agent configured for caller {caller_ext!r}, hanging up")
            await self.delete(f"/channels/{channel['id']}")
            return

        call = Call(channel["id"], caller_ext, agent_ext)
        self.calls[call.caller] = call
        try:
            await self.ari("POST", f"/channels/{call.caller}/answer")

            call.bridge = str(uuid.uuid4())
            await self.ari("POST", "/bridges", type="mixing", bridgeId=call.bridge, name=f"{BRIDGE_PREFIX}{caller_ext}")
            await self.ari("POST", f"/bridges/{call.bridge}/addChannel", channel=call.caller)
            await self.ari("POST", f"/bridges/{call.bridge}/moh", mohClass="default")
            log(f"{call} caller answered, in bridge with hold music")

            # Choose the agent channel id up front so it is known before the phone rings.
            call.agent = str(uuid.uuid4())
            self.calls[call.agent] = call
            await self.ari(
                "POST", "/channels",
                endpoint=f"PJSIP/{agent_ext}",
                app=APP,
                appArgs=f"agent,{agent_ext}",
                callerId="AlgoDial <800>",
                channelId=call.agent,
                originator=call.caller,  # agent leg inherits the caller's linkedid
                timeout=str(RING_TIMEOUT),
            )
            log(f"{call} ringing agent PJSIP/{agent_ext}")
        except Exception as e:
            await self.end(call, f"setup failed: {e}")

    async def on_agent_answer(self, channel):
        call = self.calls.get(channel["id"])
        if not call or call.ended:
            log(f"agent {channel.get('name')} answered but its call is gone, hanging up")
            await self.delete(f"/channels/{channel['id']}")
            return
        try:
            await self.ari("DELETE", f"/bridges/{call.bridge}/moh", missing_ok=True)
            await self.ari("POST", f"/bridges/{call.bridge}/addChannel", channel=call.agent)
            log(f"{call} connected, both legs in bridge {call.bridge}")
        except Exception as e:
            await self.end(call, f"connect failed: {e}")

    async def end(self, call, reason):
        if call.ended:
            return
        call.ended = True
        log(f"{call} ending: {reason}")
        for leg in (call.caller, call.agent):
            if leg:
                self.calls.pop(leg, None)
                await self.delete(f"/channels/{leg}")
        if call.bridge:
            await self.delete(f"/bridges/{call.bridge}")
        log(f"{call} cleaned up")

    async def delete(self, path):
        """DELETE that never raises, so one failed step can't stop the rest of a cleanup."""
        try:
            await self.ari("DELETE", path, missing_ok=True)
        except Exception as e:
            log(f"cleanup failed: {e}")

    async def reconcile(self, connected_at):
        """Asterisk is the truth for live calls: after (re)connecting, events may have been
        missed, so compare memory with what really exists and fix the difference."""
        channels = {c["id"]: c for c in await self.ari("GET", "/channels")}
        bridges = {b["id"]: b for b in await self.ari("GET", "/bridges")}

        # Calls in memory that lost a leg or their bridge while we weren't listening.
        for call in set(self.calls.values()):
            legs = [leg for leg in (call.caller, call.agent) if leg]
            if call.bridge not in bridges or any(leg not in channels for leg in legs):
                await self.end(call, "missing after reconnect")

        # Our bridges that no call owns (the brain restarted): adopt a connected call, tear down the rest.
        owned = {call.bridge for call in self.calls.values()}
        torn_down = set()
        for bridge in bridges.values():
            if not bridge["name"].startswith(BRIDGE_PREFIX) or bridge["id"] in owned:
                continue
            legs = [leg for leg in bridge["channels"] if leg in channels]
            if len(legs) == 2:
                legs.sort(key=lambda leg: (stasis_args(channels[leg]) or [])[:1] != ["inbound"])
                call = Call(legs[0], extension(channels[legs[0]]), extension(channels[legs[1]]))
                call.agent, call.bridge = legs[1], bridge["id"]
                self.calls[call.caller] = self.calls[call.agent] = call
                log(f"{call} adopted from Asterisk, bridge {call.bridge}")
            else:
                log(f"orphan bridge {bridge['id']} with {len(legs)} channel(s), tearing down")
                for leg in legs:
                    self.calls.pop(leg, None)
                    torn_down.add(leg)
                    await self.delete(f"/channels/{leg}")
                await self.delete(f"/bridges/{bridge['id']}")

        # Channels in our app that nothing drives. Ones created since we connected are skipped:
        # their StasisStart is still waiting in the event stream.
        for channel in channels.values():
            if (channel["id"] in self.calls or channel["id"] in torn_down or stasis_args(channel) is None
                    or parse_time(channel["creationtime"]) >= connected_at):
                continue
            log(f"orphan channel {channel['name']}, hanging up")
            await self.delete(f"/channels/{channel['id']}")


def stasis_args(channel):
    """The Stasis args if this channel is in our app, else None."""
    dialplan = channel.get("dialplan", {})
    if dialplan.get("app_name") != "Stasis":
        return None
    app, *args = dialplan.get("app_data", "").split(",")
    return args if app == APP else None


def extension(channel):
    return channel["name"].split("/", 1)[-1].rsplit("-", 1)[0]


def parse_time(stamp):
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%f%z")


async def main():
    user, password = AUTH.split(":", 1)
    events_url = ARI.replace("http", "ws", 1) + "/events"
    headers = {"Authorization": aiohttp.encode_basic_auth(user, password)}
    async with aiohttp.ClientSession(headers=headers) as http:
        brain = Brain(http)
        while True:
            try:
                connected_at = datetime.now(timezone.utc)
                async with http.ws_connect(events_url, params={"app": APP}) as ws:
                    log(f"connected to ARI as app '{APP}', waiting for calls to 800...")
                    try:
                        await brain.reconcile(connected_at)
                    except Exception as e:
                        log(f"reconcile failed: {e}")
                    # One event at a time for now; Lab 5 gives each call its own queue and task.
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            await brain.handle(json.loads(msg.data))
                        except Exception as e:
                            log(f"error handling event: {e}")
            except aiohttp.ClientError as e:
                log(f"ARI connection failed: {e}")
            log("reconnecting in 3s...")
            await asyncio.sleep(3)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
