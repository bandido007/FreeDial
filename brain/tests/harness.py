"""Run the real brain (core + service) against the fake Asterisk and the fake bus."""

import asyncio
import functools
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace

from brain.core.brain import Brain
from brain.core.config import AgentConfig, Config, EntryConfig, QueueConfig
from brain.service.runtime import Runtime
from brain.sim import FakeAri, FakeBus
from contracts import messages as m
from contracts.envelope import Command

A, B, C = "agent-uid-a", "agent-uid-b", "agent-uid-c"
PHONE_A, PHONE_B, PHONE_C = "PJSIP/lab-agent-a", "PJSIP/lab-agent-b", "PJSIP/lab-agent-c"
QUEUE = QueueConfig(
    uid="queue-1", name="Support", ring_timeout=30, max_wait=60, retry_delay=1, members=(A, B)
)


BACKUP = QueueConfig(uid="queue-2", name="Backup", ring_timeout=30, max_wait=60, members=(C,))


def make_config(**queue_settings) -> Config:
    """Support (A and B) behind 800; Backup (C, a transfer target) has no entry point."""
    return Config(
        agents={
            A: AgentConfig(A, "7001", "Asha"),
            B: AgentConfig(B, "7002", "Baraka"),
            C: AgentConfig(C, "7003", "Chiku", {"billing": 6}),
        },
        queues={QUEUE.uid: replace(QUEUE, **queue_settings), BACKUP.uid: BACKUP},
        entries={"800": EntryConfig("800", QUEUE.uid)},
        skills={"billing": "topic"},
    )


def scenario(test):
    """Run an async test body on a fresh event loop, with a time limit."""

    @functools.wraps(test)
    def run(*args, **kwargs):
        asyncio.run(asyncio.wait_for(test(*args, **kwargs), 20))

    return run


async def eventually(condition, timeout: float = 3.0, what: str = "") -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            raise AssertionError(f"never happened: {what or condition}")
        await asyncio.sleep(0.01)


class Lab:
    def __init__(self, sim: FakeAri, bus: FakeBus, runtime: Runtime):
        self.sim, self.bus, self.runtime = sim, bus, runtime

    async def command(self, command_type: str, /, **data) -> Command:
        command = Command(type=command_type, data=data)
        await self.bus.send(command)
        return command

    def agent(self, uid: str) -> dict:
        return self.bus.agents.get(uid, {})

    async def ready(self, uid: str, phone: str, *, online: bool = True) -> None:
        if online:
            self.sim.registered.add(phone)
        await self.command(m.AGENT_LOGIN, agent=uid, endpoint=phone)
        await self.command(m.AGENT_READY, agent=uid)
        await eventually(lambda: self.agent(uid).get("availability") == "ready", what=f"{uid} ready")

    async def connected_call(self, uid: str = A) -> tuple[str, str]:
        """A customer calls 800 and the first agent rung answers. Returns (customer, agent leg)."""
        customer = self.sim.call_in("800")
        leg = await self.sim.next_ringing()
        self.sim.answer(leg)
        await eventually(lambda: self.agent(uid).get("activity") == "talking", what="agent talking")
        await eventually(lambda: self.sim.bridge_of(leg) is not None, what="agent in the bridge")
        return customer, leg

    async def all_clear(self) -> None:
        """Rule 8 as the lab checks it: `core show channels` and `bridge show all` are empty."""
        await eventually(
            lambda: not self.sim.channels and not self.sim.bridges, what="no channels and no bridges"
        )
        await eventually(lambda: not self.runtime.queues, what="every call queue finished")

    def ended(self) -> list[dict]:
        return self.bus.of_type(m.CALL_ENDED)


@asynccontextmanager
async def brain(sim: FakeAri | None = None, bus: FakeBus | None = None, config: Config | None = None):
    sim = sim or FakeAri()
    bus = bus or FakeBus()
    config = config or make_config()
    core = Brain(config, app="algodial", new_id=lambda: str(uuid.uuid4()))
    core.restore_agents(await bus.load_agents())

    async def load():
        return config

    runtime = Runtime(core=core, ari=sim, bus=bus, load_config=load, reconnect_delay=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(runtime.run_forever(stop))
    await eventually(runtime.connected.is_set, what="brain connected to ARI")
    try:
        yield Lab(sim, bus, runtime)
    finally:
        stop.set()
        await task
