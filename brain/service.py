"""Wires the brain together: core decisions, adapters for the outside world.

M0: check that ARI and Redis are reachable, then wait to be stopped.
Call handling (ARI events, per-call tasks, reconcile) arrives in M2.
"""

import asyncio
import logging
import signal
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from brain.adapters.ari import Ari
from brain.adapters.bus import Bus
from brain.adapters.config import BrainSettings

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


async def run(settings: BrainSettings) -> None:
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

    log.info("brain ready as ARI app %r (M0 skeleton: no call handling yet)", settings.ari_app)
    await stop.wait()
    log.info("brain stopped")
