"""The relay: brain events from the stream to the pages of the agents they concern.

Like Pub/Sub, it reads from "now" and keeps no group: a page that missed something
re-reads the state when it reconnects. Only browser push goes this way.
"""

import json
import logging
import threading
import time
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings

from apps.calls import bus
from apps.realtime.consumers import agent_group
from contracts.envelope import ContractError, Event, decode

log = logging.getLogger(__name__)


def agents_concerned(event: Event) -> set[str]:
    """Every event naming an agent goes to that agent's page; a transfer also to its target agent."""
    return {str(event.data[key]) for key in ("agent", "target_agent") if event.data.get(key)}


def push(layer: Any, event: Event) -> int:
    """To the pages of the agents it names, and to every supervisor page (each filters by its teams)."""
    payload = {"type": event.type, "data": event.data, "at": event.at, "event_id": event.event_id}
    targets = agents_concerned(event)
    for uid in targets:
        async_to_sync(layer.group_send)(agent_group(uid), {"type": "brain.event", "event": payload})
    async_to_sync(layer.group_send)(bus.SUPERVISORS_GROUP, {"type": "brain.event", "event": payload})
    return len(targets)


def follow_live_calls(layer: Any, stop=lambda: False) -> None:
    """The brain's live-call changes (Redis Pub/Sub) to the supervisor pages. Runs in its own thread."""
    while not stop():
        try:
            listener = bus.client().pubsub(ignore_subscribe_messages=True)
            listener.subscribe(settings.BUS_LIVE_CHANNEL)
            while not stop():
                message = listener.get_message(timeout=1.0)
                if message and message.get("type") == "message":
                    change = json.loads(message["data"])
                    async_to_sync(layer.group_send)(
                        bus.SUPERVISORS_GROUP,
                        {"type": "live.call", "call": change["call"], "snapshot": change["snapshot"]},
                    )
        except Exception as exc:  # Redis away: pages re-read the live list when they reconnect
            log.error("live calls channel unavailable: %s", exc)
            time.sleep(1)


def run(*, stop=lambda: False, start: str = "$", block_ms: int = 5000) -> None:
    layer = get_channel_layer()
    threading.Thread(target=follow_live_calls, args=(layer, stop), name="live-calls", daemon=True).start()
    redis = bus.client()
    last = start
    log.info("live relay reading %s", settings.BUS_EVENTS_STREAM)
    while not stop():
        try:
            reply = redis.xread({settings.BUS_EVENTS_STREAM: last}, count=200, block=block_ms)
        except Exception as exc:  # Redis away: try again shortly, pages re-read state on reconnect
            log.error("events stream unavailable: %s", exc)
            time.sleep(1)
            continue
        for _stream, entries in reply or []:  # type: ignore[union-attr]
            for entry_id, fields in entries:
                last = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                try:
                    message = decode((fields.get(bus.FIELD.encode()) or b"").decode())
                except ContractError as exc:
                    log.error("skipping stream entry %s: %s", last, exc)
                    continue
                if isinstance(message, Event):
                    push(layer, message)
