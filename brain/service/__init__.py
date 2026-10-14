"""Wires the brain together: core decisions, adapters for the outside world.

``run`` checks ARI and Redis, loads the configuration, takes back the agent states
a previous brain saved, and hands over to :class:`~brain.service.runtime.Runtime`.
"""

import asyncio
import dataclasses
import json
import logging
import signal
import uuid
from dataclasses import dataclass
from typing import TextIO
from urllib.parse import urlsplit, urlunsplit

from brain.adapters.ari import Ari
from brain.adapters.bus import Bus
from brain.adapters.config import BrainSettings, load_config
from brain.core.brain import Brain
from brain.service.runtime import Runtime

log = logging.getLogger("brain")


class StartupError(Exception):
    """The brain cannot start: something it needs is unreachable."""


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def without_password(url: str) -> str:
    parts = urlsplit(url)
    if parts.password is None:
        return url
    host = (parts.hostname or "") + (f":{parts.port}" if parts.port else "")
    return urlunsplit(parts._replace(netloc=f"{parts.username or ''}:***@{host}"))


async def check(settings: BrainSettings) -> list[CheckResult]:
    """Try every connection the brain needs. Never raises: each result says ok or why not."""
    results = []

    redis_url = without_password(settings.bus_redis_url)
    try:
        async with Bus(url=settings.bus_redis_url) as bus:
            await bus.ping()
        results.append(CheckResult("Redis", True, redis_url))
    except Exception as exc:
        results.append(CheckResult("Redis", False, f"{redis_url}: {exc or type(exc).__name__}"))

    try:
        async with Ari(url=settings.ari_url, user=settings.ari_user, password=settings.ari_password) as ari:
            info = await ari.asterisk_info()
        results.append(CheckResult("ARI", True, f"{settings.ari_url}: Asterisk {info['system']['version']}"))
    except Exception as exc:
        results.append(CheckResult("ARI", False, f"{settings.ari_url}: {exc or type(exc).__name__}"))

    return results


async def run(settings: BrainSettings, record: TextIO | None = None) -> None:
    results = await check(settings)
    for result in results:
        log.log(
            logging.INFO if result.ok else logging.ERROR,
            "%s %s: %s",
            result.name,
            "ok" if result.ok else "unreachable",
            result.detail,
        )
    failed = [result.name for result in results if not result.ok]
    if failed:
        raise StartupError(f"cannot reach {', '.join(failed)}")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    config = await asyncio.to_thread(load_config)
    log.info(
        "configuration: %d agents, %d queues, entry points %s",
        len(config.agents),
        len(config.queues),
        ", ".join(sorted(config.entries)) or "none",
    )
    ari = Ari(
        url=settings.ari_url, user=settings.ari_user, password=settings.ari_password, app=settings.ari_app
    )
    bus = Bus(
        url=settings.bus_redis_url,
        commands_stream=settings.commands_stream,
        events_stream=settings.events_stream,
        agents_key=settings.agents_key,
    )
    async with ari, bus:
        core = Brain(config, app=settings.ari_app, new_id=lambda: str(uuid.uuid4()))
        saved = await bus.load_agents()
        core.restore_agents(saved)
        if record is not None:  # a replay starts a fresh core here
            start = {
                "kind": "start",
                "app": settings.ari_app,
                "config": dataclasses.asdict(config),
                "agents": saved,
            }
            record.write(json.dumps(start) + "\n")
        runtime = Runtime(
            core=core,
            ari=ari,
            bus=bus,
            load_config=lambda: asyncio.to_thread(load_config),
            consumer=settings.consumer,
            record=record,
        )
        log.info("brain ready as ARI app %r", settings.ari_app)
        await runtime.run_forever(stop)
    log.info("brain stopped")
