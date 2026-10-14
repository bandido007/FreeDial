"""The writer: brain events from the stream into Event, Call and Leg.

Each event is written in one transaction: the Event row (unique ``event_id``)
and its effect on Call and Leg. A repeat of an event already written changes
nothing, so the stream's at-least-once delivery is safe (rule 12). Events of a
type this writer does not know are still kept in the Event log.
"""

import logging
import time
from datetime import datetime
from typing import Any

from django.db import DatabaseError, close_old_connections, transaction

from apps.calls.bus import StreamReader
from apps.calls.models import Call, Event, Leg, LegRole
from contracts import messages as m
from contracts.envelope import ContractError, decode
from contracts.envelope import Event as Message

log = logging.getLogger(__name__)

GROUP = "writer"


@transaction.atomic
def write(message: Message) -> bool:
    """Store one event. False when it was already stored."""
    data = message.data
    at = datetime.fromisoformat(message.at)
    _, created = Event.objects.get_or_create(
        event_id=message.event_id,
        defaults={
            "type": message.type,
            "at": at,
            "call_id": str(data.get("call") or ""),
            "leg_id": str(data.get("leg") or data.get("channel") or ""),
            "agent_uid": str(data.get("agent") or ""),
            "command_id": message.command_id or "",
            "data": data,
        },
    )
    if created:
        _project(message.type, data, at)
    return created


def _call(linkedid: str, at: datetime) -> Call:
    call, _ = Call.objects.get_or_create(linkedid=linkedid, defaults={"started_at": at})
    return call


def _project(kind: str, data: dict[str, Any], at: datetime) -> None:
    if kind == m.CALL_STARTED:
        call = _call(data["call"], at)
        call.entry, call.queue = data["entry"], data["queue"]
        call.caller, call.caller_name = data["caller"], data.get("caller_name", "")
        call.started_at = call.started_at or at
        call.save()
        Leg.objects.get_or_create(
            uniqueid=data["leg"],
            defaults={
                "call": call,
                "role": LegRole.CUSTOMER,
                "endpoint": data["caller"],
                "sip_call_id": data["sip_call_id"],
                "started_at": at,
                "answered_at": at,
            },
        )
    elif kind == m.CALL_OFFERED:
        Leg.objects.get_or_create(
            uniqueid=data["leg"],
            defaults={
                "call": _call(data["call"], at),
                "role": LegRole.AGENT,
                "endpoint": data.get("endpoint") or "",
                "agent_uid": data["agent"],
                "started_at": at,
            },
        )
    elif kind == m.CALL_OFFER_FAILED:
        Leg.objects.filter(uniqueid=data["leg"]).update(
            outcome=data["reason"], cause=data.get("cause"), ended_at=at
        )
    elif kind == m.CALL_CONNECTED:
        Leg.objects.filter(uniqueid=data["leg"]).update(
            answered_at=at, outcome="answered", sip_call_id=data.get("sip_call_id") or ""
        )
        call = _call(data["call"], at)
        call.answered_at = call.answered_at or at
        call.agent_uid = data["agent"] or ""
        call.save()
    elif kind == m.LEG_ENDED:
        leg = Leg.objects.filter(uniqueid=data["leg"]).first()
        if leg is not None and leg.ended_at is None:
            leg.ended_at = at
            leg.cause = data.get("cause") if leg.cause is None else leg.cause
            if leg.role == LegRole.AGENT and leg.answered_at is None and not leg.outcome:
                leg.outcome = "cancelled"  # still ringing when the call ended
            leg.save()
    elif kind == m.CALL_ENDED:
        call = _call(data["call"], at)
        call.ended_at, call.result, call.end_reason = at, data["result"], data["reason"][:200]
        call.agent_uid = data.get("agent") or call.agent_uid
        call.save()


def handle(reader: StreamReader, entry_id: str, raw: str) -> None:
    """Write one stream entry and acknowledge it. A database error leaves it unacknowledged."""
    try:
        message = decode(raw)
        if not isinstance(message, Message):
            raise ContractError("a command on the events stream")
        m.check(message)
    except ContractError as exc:
        log.error("skipping stream entry %s: %s", entry_id, exc)  # never retried: it would fail forever
        reader.ack(entry_id)
        return
    write(message)
    reader.ack(entry_id)


def drain(reader: StreamReader, *, pending: bool, block_ms: int = 5000) -> int:
    count = 0
    for entry_id, raw in reader.entries(pending=pending, block_ms=block_ms):
        handle(reader, entry_id, raw)
        count += 1
    return count


def run(consumer: str, *, stop=lambda: False) -> None:
    reader = StreamReader(group=GROUP, consumer=consumer)
    log.info("writer %s reading %s", consumer, reader.stream)
    pending = True
    while not stop():
        try:
            count = drain(reader, pending=pending)
            pending = pending and count > 0  # first catch up on what this consumer never acknowledged
        except DatabaseError as exc:
            log.error("database error, retrying in 2 s: %s", exc)
            close_old_connections()
            pending = True
            time.sleep(2)
