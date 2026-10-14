"""The worker on the bus: it reads ``ai.started`` from the events stream (consumer group "ai") and
sends its commands (ai.handoff, ai.hangup) on the commands stream, like the web does."""

import logging
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as redis
from django.conf import settings
from redis.exceptions import ResponseError

from contracts import messages
from contracts.envelope import Command, ContractError, Event, decode, encode

log = logging.getLogger("aiworker")

FIELD = "message"  # one encoded envelope per stream entry, as everywhere on the bus
GROUP = "ai"


class Bus:
    def __init__(self, url: str | None = None, consumer: str | None = None):
        self.redis = redis.Redis.from_url(url or settings.BUS_REDIS_URL)
        self.consumer = consumer or settings.AI_CONSUMER

    async def join(self) -> None:
        try:  # new group: only calls from now on (a call from before has long timed out to a person)
            await self.redis.xgroup_create(settings.BUS_EVENTS_STREAM, GROUP, id="$", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def events(self) -> AsyncIterator[tuple[str, Event | None]]:
        """(entry id, event) forever: first this consumer's unacknowledged entries, then new ones."""
        cursor = "0"
        while True:
            reply = await self.redis.xreadgroup(
                GROUP, self.consumer, {settings.BUS_EVENTS_STREAM: cursor}, count=50, block=5000
            )
            entries = reply[0][1] if reply else []
            if cursor == "0" and not entries:
                cursor = ">"
                continue
            for entry_id, fields in entries:
                raw = fields.get(FIELD.encode()) or fields.get(FIELD)
                try:
                    message = decode(raw.decode() if isinstance(raw, bytes) else str(raw))
                except (ContractError, AttributeError) as exc:
                    log.warning("skipping a bad entry %s: %s", entry_id, exc)
                    message = None
                yield (
                    (entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)),
                    (message if isinstance(message, Event) else None),
                )

    async def ack(self, entry_id: str) -> None:
        await self.redis.xack(settings.BUS_EVENTS_STREAM, GROUP, entry_id)

    async def send(self, kind: str, data: dict[str, Any]) -> str:
        command = Command(type=kind, data=data)
        messages.check(command)
        await self.redis.xadd(settings.BUS_COMMANDS_STREAM, {FIELD: encode(command)})
        return command.command_id

    async def close(self) -> None:
        await self.redis.aclose()
