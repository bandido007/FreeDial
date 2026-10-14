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
from apps.calls.models import (
    Call,
    CallbackTask,
    Event,
    InteractionPart,
    Leg,
    LegRole,
    PartKind,
    RecordingStatus,
    Transfer,
    TransferStatus,
)
from contracts import messages as m
from contracts.envelope import ContractError, decode
from contracts.envelope import Event as Message

log = logging.getLogger(__name__)

GROUP = "writer"
TRANSFER_ENDINGS = {
    m.TRANSFER_ANSWERED: TransferStatus.ANSWERED,
    m.TRANSFER_COMPLETED: TransferStatus.COMPLETED,
    m.TRANSFER_CANCELLED: TransferStatus.CANCELLED,
    m.TRANSFER_FAILED: TransferStatus.FAILED,
    m.CONFERENCE_STARTED: TransferStatus.CONFERENCE,
}


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
        InteractionPart.objects.create(
            call=call, kind=PartKind.AGENT, started_at=at, agent_uid=data["agent"] or ""
        )
    elif kind == m.AI_STARTED:
        call = _call(data["call"], at)
        Leg.objects.get_or_create(
            uniqueid=data["leg"],
            defaults={"call": call, "role": LegRole.AI, "endpoint": "AI", "started_at": at},
        )
        InteractionPart.objects.create(
            call=call, kind=PartKind.AI, started_at=at, ai_agent_uid=data["ai_agent"]
        )
    elif kind == m.AI_CONNECTED:
        Leg.objects.filter(uniqueid=data["leg"]).update(answered_at=at, outcome="answered")
    elif kind == m.AI_ENDED:
        part = InteractionPart.objects.filter(
            call__linkedid=data["call"], kind=PartKind.AI, ended_at=None
        ).last()
        if part is not None:
            part.ended_at, part.outcome, part.reason = at, data["outcome"], str(data["reason"])[:200]
            part.summary = str(data.get("summary") or "")
            part.transcript = data.get("transcript") or []
            part.save()
    elif kind == m.AGENT_WRAPPED_UP:
        part = (
            InteractionPart.objects.filter(
                call__linkedid=data["call"], kind=PartKind.AGENT, agent_uid=data["agent"]
            )
            .order_by("-started_at", "-id")
            .first()
        )
        if part is not None and data.get("code"):
            part.wrapup_code = str(data["code"])[:120]
            part.save(update_fields=["wrapup_code"])
    elif kind == m.TRANSFER_STARTED:
        Transfer.objects.get_or_create(
            transfer_id=data["transfer"],
            defaults={
                "call": _call(data["call"], at),
                "kind": data["kind"],
                "target_kind": data["target_kind"],
                "target": str(data["target"]),
                "by_agent_uid": data["agent"] or "",
                "target_agent_uid": data.get("target_agent") or "",
                "started_at": at,
            },
        )
    elif kind in TRANSFER_ENDINGS:
        status = TRANSFER_ENDINGS[kind]
        fields: dict[str, Any] = {"reason": str(data.get("reason") or "")[:200]}
        if status == TransferStatus.ANSWERED:
            fields["answered_at"] = at
        else:
            fields["ended_at"] = at
        Transfer.objects.filter(transfer_id=data["transfer"]).update(status=status, **fields)
    elif kind == m.CALLBACK_REQUESTED:
        CallbackTask.objects.get_or_create(
            linkedid=data["call"],
            defaults={
                "call": _call(data["call"], at),
                "queue": data["queue"],
                "caller": data["caller"] or "",
                "caller_name": data.get("caller_name") or "",
                "language": data.get("language") or "",
                "recording": data["recording"] or "",
                "duration": data["duration"],
                "created_at": at,
            },
        )
    elif kind == m.ROUTING_TICKET:
        Call.objects.filter(linkedid=data["call"]).update(queue=data["queue"])
    elif kind == m.CALL_OVERFLOWED:
        Call.objects.filter(linkedid=data["call"]).update(queue=data["to_queue"])  # the queue it ends in
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
        if (
            call.recording_status == RecordingStatus.NONE
        ):  # every 8xx call is recorded (M7); kept after hangup
            call.recording_status = RecordingStatus.PENDING
        call.save()
        call.parts.filter(ended_at=None).update(ended_at=at)


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
