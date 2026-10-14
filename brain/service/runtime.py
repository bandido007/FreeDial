"""The running brain: hands every input to the core and carries out what it decides.

* One reader of the ARI event WebSocket. On every (re)connect it reads ``/channels``
  and ``/bridges`` and lets the core reconcile (rule 1) before handling any event.
* One reader of the commands stream (consumer group ``brain``), acknowledging each
  command after it is handled; a repeated ``command_id`` is skipped (rule 12).
* Decisions are instant (the core never waits). The ARI actions they produce go to
  the call's own queue, run in order by that call's own task, so a slow or failing
  request holds up only its own call (rule 7).
* Events and agent states go to Redis through one outbox, in decision order. A Redis
  outage holds them in memory and never stops a call.
"""

import asyncio
import dataclasses
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TextIO

from brain.adapters.ari import AriLike, perform
from brain.core import actions as a
from brain.core.brain import Brain
from brain.core.config import Config
from contracts import messages
from contracts.envelope import Command, ContractError, Event, decode

log = logging.getLogger("brain")

GROUP = "brain"
COMMAND_MAX_AGE = 300  # seconds; an older command (sent while the brain was down) is refused
SEEN_COMMANDS = 10_000

LOGGED_EVENTS = {
    messages.CALL_STARTED: "call {call} from {caller} to {entry} (queue {queue})",
    messages.CALL_OFFERED: "call {call} ringing agent {agent} on {endpoint}",
    messages.CALL_OFFER_FAILED: "call {call}: agent {agent} did not take it ({reason}, cause {cause})",
    messages.CALL_CONNECTED: "call {call} connected to agent {agent}",
    messages.CALL_ENDED: "call {call} ended: {result} ({reason})",
    messages.STASIS_REJECTED: "channel {channel} hung up: {reason} (args {args})",
    messages.AGENT_STATE: "agent {number}: {availability}/{activity} {reason}",
    messages.COMMAND_REJECTED: "command {type} refused: {reason}",
    messages.BRAIN_RECONCILED: "reconciled: adopted {adopted}, hung up {hung_up}, deleted bridges {bridges_deleted}",
}


@dataclass(frozen=True)
class LiveCall:
    """A changed call for the supervisor pages (None: it ended)."""

    call: str
    snapshot: dict[str, Any] | None


class CallQueue:
    """One call's ARI actions, carried out in order by the call's own task."""

    def __init__(self, runtime: "Runtime", key: str):
        self.runtime = runtime
        self.key = key
        self.batches: asyncio.Queue[list[a.AriAction]] = asyncio.Queue()
        self.task = asyncio.create_task(self.run(), name=f"call {key}")

    async def run(self) -> None:
        closing = False
        while not (closing and self.batches.empty()):
            batch = await self.batches.get()
            failed = False
            for action in batch:
                if isinstance(action, a.Close):
                    closing = True
                    continue
                if failed and not action.cleanup:
                    continue  # it depended on what failed; the core decides what happens instead
                try:
                    await perform(self.runtime.ari, action)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if action.cleanup:  # rule 8: one failed step never stops the rest of a cleanup
                        log.warning("cleanup step %s on %s failed: %s", type(action).__name__, self.key, exc)
                        continue
                    failed = True
                    log.warning("%s on call %s failed: %s", type(action).__name__, self.key, exc)
                    self.report_failure(action, str(exc))
        if self.runtime.queues.get(self.key) is self:
            del self.runtime.queues[self.key]

    def report_failure(self, action: a.AriAction, error: str) -> None:
        core = self.runtime.core
        self.runtime.decide(
            "failed",
            {"action": serialize(action), "error": error},
            lambda now: core.on_failed(action, error, now),
        )


class Runtime:
    def __init__(
        self,
        *,
        core: Brain,
        ari: AriLike,
        bus: Any,
        load_config: Callable[[], Awaitable[Config]],
        consumer: str = "brain-1",
        clock: Callable[[], float] = time.time,
        reconnect_delay: float = 3.0,
        record: TextIO | None = None,
    ):
        self.core = core
        self.ari = ari
        self.bus = bus
        self.load_config = load_config
        self.consumer = consumer
        self.clock = clock
        self.reconnect_delay = reconnect_delay
        self.record = record
        self.queues: dict[str, CallQueue] = {}
        self.timers: dict[str, asyncio.TimerHandle] = {}
        self.outbox: asyncio.Queue[Event | a.SaveAgent | LiveCall] = asyncio.Queue()
        self._live: dict[str, dict[str, Any]] = {}  # the call views last published
        self.connected = asyncio.Event()
        self.reconciled = 0  # how many times the brain (re)connected and reconciled
        self._seen: deque[str] = deque(maxlen=SEEN_COMMANDS)
        self._seen_set: set[str] = set()
        self._ids: list[str] | None = None
        self._new_id = core.new_id
        core.new_id = self._capture_id

    # ── decisions ─────────────────────────────────────────────────────────
    def _capture_id(self) -> str:
        new = self._new_id()
        if self._ids is not None:
            self._ids.append(new)
        return new

    def decide(
        self,
        kind: str,
        given: dict[str, Any],
        decision: Callable[[float], list[a.Action]],
        command_id: str | None = None,
    ) -> list[a.Action]:
        """Run one core decision now and carry out its actions."""
        now = self.clock()
        self._ids = []
        try:
            actions = decision(now)
        finally:
            ids, self._ids = self._ids, None
        if self.record is not None:
            line = {
                "kind": kind,
                "now": now,
                "input": given,
                "ids": ids,
                "actions": [serialize(x) for x in actions],
            }
            self.record.write(json.dumps(line, default=str) + "\n")
            self.record.flush()
        self.carry_out(actions, command_id)
        self._publish_live_calls()
        return actions

    def _publish_live_calls(self) -> None:
        now = self.core.live_calls()
        for call, view in now.items():
            if self._live.get(call) != view:
                self.outbox.put_nowait(LiveCall(call, view))
        for call in self._live.keys() - now.keys():
            self.outbox.put_nowait(LiveCall(call, None))
        self._live = now

    def carry_out(self, actions: list[a.Action], command_id: str | None = None) -> None:
        batches: dict[str, list[a.AriAction]] = {}
        for action in actions:
            if isinstance(action, a.AriAction):
                batches.setdefault(action.call, []).append(action)
            elif isinstance(action, a.Emit):
                event = Event(type=action.type, data=action.data, command_id=action.command_id or command_id)
                self._log_event(event)
                self.outbox.put_nowait(event)
            elif isinstance(action, a.SaveAgent):
                self.outbox.put_nowait(action)
            elif isinstance(action, a.SetTimer):
                self._cancel_timer(action.key)
                loop = asyncio.get_running_loop()
                self.timers[action.key] = loop.call_later(action.seconds, self._timer_fired, action.key)
            elif isinstance(action, a.CancelTimer):
                self._cancel_timer(action.key)
        for key, batch in batches.items():
            queue = self.queues.get(key)
            if queue is None:
                queue = self.queues[key] = CallQueue(self, key)
            queue.batches.put_nowait(batch)

    def _cancel_timer(self, key: str) -> None:
        handle = self.timers.pop(key, None)
        if handle is not None:
            handle.cancel()

    def _timer_fired(self, key: str) -> None:
        self.timers.pop(key, None)
        self.decide("timer", {"key": key}, lambda now: self.core.on_timer(key, now))

    def _log_event(self, event: Event) -> None:
        template = LOGGED_EVENTS.get(event.type)
        if template:
            try:
                log.info(template.format(**event.data))
            except (KeyError, IndexError):
                log.info("%s %s", event.type, event.data)

    # ── ARI ───────────────────────────────────────────────────────────────
    async def follow_ari(self) -> None:
        while True:
            try:
                async with self.ari.events() as stream:
                    connected_at = self.clock()
                    log.info("connected to ARI, reconciling with what Asterisk has")
                    await self.reconcile(connected_at)
                    self.connected.set()
                    async for event in stream:
                        self.on_ari_event(event)
                log.warning("ARI event stream closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("ARI connection lost or failed: %s", exc or type(exc).__name__)
            self.connected.clear()
            await asyncio.sleep(self.reconnect_delay)

    async def reconcile(self, connected_at: float) -> None:
        channels = await self.ari.request("GET", "/channels") or []
        bridges = await self.ari.request("GET", "/bridges") or []
        self.decide(
            "reconcile",
            {"channels": channels, "bridges": bridges, "connected_at": connected_at},
            lambda now: self.core.reconcile(channels, bridges, connected_at, now),
        )
        self.reconciled += 1

    def on_ari_event(self, event: dict[str, Any]) -> None:
        if event.get("type") == "StasisStart":
            # Rule 6: the join keys of every channel that enters the app.
            channel = event.get("channel") or {}
            args = [str(arg) for arg in event.get("args") or []]
            linkedid = ""
            if args[:1] == ["inbound"] and len(args) > 3:
                linkedid = args[3]
            elif args[:1] == ["agent"] and len(args) > 1:
                linkedid = args[1]
            log.info(
                "StasisStart channel=%s sip_call_id=%s args=%s linkedid=%s",
                channel.get("id"),
                channel.get("protocol_id"),
                args,
                linkedid or "?",
            )
        self.decide("ari", {"event": event}, lambda now: self.core.on_ari(event, now))

    # ── commands ──────────────────────────────────────────────────────────
    async def follow_commands(self) -> None:
        pending = True  # first, whatever this consumer read but never acknowledged
        joined = False
        while True:
            try:
                if not joined:
                    await self.bus.join(GROUP)
                    joined = True
                entries = await self.bus.read_commands(GROUP, self.consumer, pending=pending)
                if pending and not entries:
                    pending = False
                    continue
                for entry_id, raw in entries:
                    await self.on_command(raw)
                    await self.bus.ack(GROUP, entry_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("reading commands failed: %s", exc or type(exc).__name__)
                pending = True
                await asyncio.sleep(1)

    async def on_command(self, raw: str) -> None:
        try:
            command = decode(raw)
            if not isinstance(command, Command):
                raise ContractError("an event on the commands stream")
            messages.check(command)
        except ContractError as exc:
            log.error("skipping a malformed command: %s", exc)
            return
        if command.command_id in self._seen_set:
            return  # delivered twice: handled already
        if len(self._seen) == self._seen.maxlen:
            self._seen_set.discard(self._seen[0])
        self._seen.append(command.command_id)
        self._seen_set.add(command.command_id)

        sent = _epoch(command.sent_at)
        if sent is not None and self.clock() - sent > COMMAND_MAX_AGE:
            self.carry_out(
                [
                    a.Emit(
                        messages.COMMAND_REJECTED,
                        {"command_id": command.command_id, "type": command.type, "reason": "expired"},
                    )
                ],
                command.command_id,
            )
            return

        if command.type == messages.CONFIG_CHANGED:
            config = await self.load_config()
            self.decide(
                "config",
                {"config": dataclasses.asdict(config)},
                lambda now: self.core.load_config(config, now),
            )
            log.info(
                "configuration reloaded: %d agents, %d queues, %d entry points",
                len(config.agents),
                len(config.queues),
                len(config.entries),
            )
            return
        self.decide(
            "command",
            {"type": command.type, "data": command.data, "command_id": command.command_id},
            lambda now: self.core.on_command(command.type, command.data, command.command_id, now),
            command_id=command.command_id,
        )

    # ── Redis outbox ──────────────────────────────────────────────────────
    async def publish_forever(self) -> None:
        while True:
            item = await self.outbox.get()
            while True:
                try:
                    if isinstance(item, Event):
                        await self.bus.publish(item)
                    elif isinstance(item, LiveCall):
                        await self.bus.save_call(item.call, item.snapshot)
                    else:
                        await self.bus.save_agent(item.agent, item.state)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.error("Redis unavailable, holding %d messages: %s", self.outbox.qsize() + 1, exc)
                    await asyncio.sleep(1)

    # ── life ──────────────────────────────────────────────────────────────
    async def run_forever(self, stop: asyncio.Event) -> None:
        try:
            await self.bus.clear_calls()  # rebuilt by the first reconcile
        except Exception as exc:
            log.error("could not clear the live call list: %s", exc)
        readers = [
            asyncio.create_task(self.follow_ari(), name="ari"),
            asyncio.create_task(self.follow_commands(), name="commands"),
        ]
        publisher = asyncio.create_task(self.publish_forever(), name="outbox")
        try:
            await stop.wait()
        finally:
            await self.shutdown(readers, publisher)

    async def shutdown(self, readers: list[asyncio.Task], publisher: asyncio.Task) -> None:
        """Calls stay in Asterisk: the next brain adopts them. Finish what is in flight, then stop."""
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        for handle in self.timers.values():
            handle.cancel()
        in_flight = [queue.task for queue in self.queues.values()]
        if in_flight:
            await asyncio.wait(in_flight, timeout=2)
        for task in in_flight:
            task.cancel()
        deadline = time.monotonic() + 3
        while not self.outbox.empty() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        publisher.cancel()
        await asyncio.gather(publisher, *in_flight, return_exceptions=True)


def serialize(action: a.Action) -> dict[str, Any]:
    return {"action": type(action).__name__, **dataclasses.asdict(action)}


def deserialize(data: dict[str, Any]) -> a.Action:
    fields = dict(data)
    return getattr(a, fields.pop("action"))(**fields)


def _epoch(stamp: str) -> float | None:
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError):
        return None
