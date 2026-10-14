"""An in-memory bus with the brain's Bus methods: events, commands (one consumer group), agent states."""

import asyncio
import json
from typing import Any

from contracts.envelope import Command, Event, decode, encode


class FakeBus:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.agents: dict[str, dict[str, Any]] = {}
        self.calls: dict[str, dict[str, Any]] = {}
        self.live: list[dict[str, Any]] = []  # what was published for supervisor pages
        self._commands: list[tuple[str, str]] = []  # (entry id, raw)
        self._delivered = 0  # entries handed to the group so far
        self._pending: dict[str, str] = {}  # delivered, not acknowledged
        self._arrived = asyncio.Event()
        self.down = False  # True: every call fails, like Redis being unreachable

    def _check(self) -> None:
        if self.down:
            raise ConnectionError("fake Redis is down")

    async def ping(self) -> bool:
        self._check()
        return True

    async def publish(self, event: Event) -> None:
        self._check()
        self.events.append(decode(encode(event)))  # type: ignore[arg-type]

    async def save_agent(self, uid: str, state: dict[str, Any] | None) -> None:
        self._check()
        if state is None:
            self.agents.pop(uid, None)
        else:
            self.agents[uid] = json.loads(json.dumps(state))

    async def save_call(self, call: str, snapshot: dict[str, Any] | None) -> None:
        self._check()
        if snapshot is None:
            self.calls.pop(call, None)
        else:
            self.calls[call] = json.loads(json.dumps(snapshot))
        self.live.append({"type": "call.snapshot", "call": call, "snapshot": snapshot})

    async def clear_calls(self) -> None:
        self._check()
        self.calls.clear()

    async def load_agents(self) -> list[dict[str, Any]]:
        self._check()
        return list(self.agents.values())

    async def join(self, group: str) -> None:
        self._check()

    async def send(self, command: Command | str) -> None:
        raw = command if isinstance(command, str) else encode(command)
        self._commands.append((f"{len(self._commands) + 1}-0", raw))
        self._arrived.set()

    async def read_commands(
        self, group: str, consumer: str, *, pending: bool, block_ms: int = 5000
    ) -> list[tuple[str, str]]:
        self._check()
        if pending:
            return list(self._pending.items())
        if self._delivered >= len(self._commands):
            self._arrived.clear()
            try:
                await asyncio.wait_for(self._arrived.wait(), block_ms / 1000)
            except TimeoutError:
                return []
        new = self._commands[self._delivered :]
        self._delivered = len(self._commands)
        self._pending.update(new)
        return new

    async def ack(self, group: str, entry_id: str) -> None:
        self._pending.pop(entry_id, None)

    # ── inspection ────────────────────────────────────────────────────────
    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [event.data for event in self.events if event.type == kind]
