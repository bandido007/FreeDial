"""The bus: Redis Streams between web, brain and writer (redis-py, asyncio).

* Events: the brain appends to ``algodial:events`` (XADD, capped).
* Commands: the brain reads ``algodial:commands`` through the consumer group ``brain``
  and acknowledges each one after handling it, so none is lost if it stops mid-way.
* Live agent state: one hash, agent uid -> JSON. The web reads it; a restarted brain
  starts from it.
* Live calls: one hash, call -> JSON, plus a Pub/Sub message per change for supervisor pages.
  Nothing that must not be lost goes through Pub/Sub: a page re-reads the hash when it connects.
"""

import json
from typing import Any

import redis.asyncio as redis
from redis.exceptions import ResponseError

from contracts.envelope import Command, Event, encode

FIELD = "message"  # each stream entry holds one encoded envelope under this field


class Bus:
    def __init__(
        self,
        *,
        url: str,
        commands_stream: str = "algodial:commands",
        events_stream: str = "algodial:events",
        agents_key: str = "algodial:agents",
        calls_key: str = "algodial:calls",
        live_channel: str = "algodial:live",
        events_maxlen: int = 1_000_000,
        timeout: float = 2.0,
    ):
        self.commands_stream = commands_stream
        self.events_stream = events_stream
        self.agents_key = agents_key
        self.calls_key = calls_key
        self.live_channel = live_channel
        self.events_maxlen = events_maxlen
        self._redis = redis.Redis.from_url(url, socket_connect_timeout=timeout, socket_timeout=timeout + 8)

    async def __aenter__(self) -> "Bus":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._redis.aclose()

    async def ping(self) -> bool:
        return bool(await self._redis.ping())

    async def publish(self, event: Event) -> None:
        await self._redis.xadd(
            self.events_stream, {FIELD: encode(event)}, maxlen=self.events_maxlen, approximate=True
        )

    async def save_agent(self, uid: str, state: dict[str, Any] | None) -> None:
        if state is None:
            await self._redis.hdel(self.agents_key, uid)
        else:
            await self._redis.hset(self.agents_key, uid, json.dumps(state))

    async def load_agents(self) -> list[dict[str, Any]]:
        saved = await self._redis.hgetall(self.agents_key)
        return [json.loads(value) for value in saved.values()]

    async def save_call(self, call: str, snapshot: dict[str, Any] | None) -> None:
        """Keep the live call list (supervisor pages read it) and push the change (Pub/Sub: browser push only)."""
        if snapshot is None:
            await self._redis.hdel(self.calls_key, call)
        else:
            await self._redis.hset(self.calls_key, call, json.dumps(snapshot))
        await self._redis.publish(
            self.live_channel, json.dumps({"type": "call.snapshot", "call": call, "snapshot": snapshot})
        )

    async def clear_calls(self) -> None:
        """A starting brain rebuilds the live call list from what Asterisk has."""
        await self._redis.delete(self.calls_key)

    async def join(self, group: str) -> None:
        """Create the consumer group (and the stream) once; later calls do nothing."""
        try:
            await self._redis.xgroup_create(self.commands_stream, group, id="$", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def read_commands(
        self, group: str, consumer: str, *, pending: bool, block_ms: int = 5000
    ) -> list[tuple[str, str]]:
        """(entry id, raw message) pairs. ``pending`` re-reads what this consumer got but never acknowledged."""
        reply = await self._redis.xreadgroup(
            group,
            consumer,
            {self.commands_stream: "0" if pending else ">"},
            count=100,
            block=None if pending else block_ms,
        )
        out = []
        for _stream, entries in reply or []:
            for entry_id, fields in entries:
                raw = fields.get(FIELD.encode()) or fields.get(FIELD) or b""
                out.append((_text(entry_id), _text(raw)))
        return out

    async def ack(self, group: str, entry_id: str) -> None:
        await self._redis.xack(self.commands_stream, group, entry_id)

    async def send(self, command: Command) -> None:
        """Used by tests and tools; the web has its own sender (apps.calls.bus)."""
        await self._redis.xadd(self.commands_stream, {FIELD: encode(command)})


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
