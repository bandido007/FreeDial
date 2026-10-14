"""The web's side of the bus: send commands to the brain, read the event stream.

The web never imports the brain; it asks with a command (contracts) and the brain
decides (rule 2). Every command carries a fresh ``command_id`` (rule 12).
"""

import logging
from collections.abc import Iterator
from functools import lru_cache
from typing import Any

import redis
from django.conf import settings
from django.db import transaction
from redis.exceptions import ResponseError

from contracts import messages
from contracts.envelope import Command, encode

log = logging.getLogger(__name__)

FIELD = "message"  # one encoded envelope per stream entry, the same field the brain uses
SUPERVISORS_GROUP = "supervisors"  # Channels group of every supervisor page's socket (browser push only)


@lru_cache(maxsize=1)
def client() -> redis.Redis:
    return redis.Redis.from_url(settings.BUS_REDIS_URL, socket_connect_timeout=2, socket_timeout=10)


def send_command(kind: str, data: dict[str, Any] | None = None) -> str:
    """Put a command on the stream now; returns its ``command_id``."""
    command = Command(type=kind, data=dict(data or {}))
    messages.check(command)
    client().xadd(settings.BUS_COMMANDS_STREAM, {FIELD: encode(command)})
    return command.command_id


def send_command_on_commit(kind: str, data: dict[str, Any] | None = None) -> str:
    """Send a command once the current transaction commits (at once outside one); returns its id."""
    command = Command(type=kind, data=dict(data or {}))
    messages.check(command)
    transaction.on_commit(lambda: client().xadd(settings.BUS_COMMANDS_STREAM, {FIELD: encode(command)}))
    return command.command_id


def _send_config_changed() -> None:
    try:
        send_command(messages.CONFIG_CHANGED)
    except redis.RedisError as exc:
        log.error("config.changed not sent (brain reloads at its next start): %s", exc)


def config_changed() -> None:
    """Tell the brain to reload its configuration once the current transaction commits.

    Several saves in one transaction (an admin page with inlines) send one command.
    A Redis outage never blocks a save: the brain also loads the configuration at start.
    """
    queued = transaction.get_connection().run_on_commit
    if any(callback is _send_config_changed for _savepoints, callback, *_ in queued):
        return
    transaction.on_commit(_send_config_changed)


class StreamReader:
    """Read the events stream through a consumer group, acknowledging after each handled entry.

    At-least-once: an entry handled but not acknowledged (a crash in between) comes back,
    so handlers must be idempotent.
    """

    def __init__(self, *, group: str, consumer: str, stream: str | None = None, start: str = "0"):
        self.group = group
        self.consumer = consumer
        self.stream = stream or settings.BUS_EVENTS_STREAM
        self.redis = client()
        try:
            self.redis.xgroup_create(self.stream, group, id=start, mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def entries(self, *, pending: bool, block_ms: int = 5000, count: int = 200) -> Iterator[tuple[str, str]]:
        reply = self.redis.xreadgroup(
            self.group,
            self.consumer,
            {self.stream: "0" if pending else ">"},
            count=count,
            block=None if pending else block_ms,
        )
        for _stream, entries in reply or []:  # type: ignore[union-attr]  # sync client: a list
            for entry_id, fields in entries:
                raw = fields.get(FIELD.encode()) or b""
                yield _text(entry_id), _text(raw)

    def ack(self, entry_id: str) -> None:
        self.redis.xack(self.stream, self.group, entry_id)


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
