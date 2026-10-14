"""The writer: every brain event lands in Event once, and Call and Leg follow from them."""

import pytest
from django.conf import settings

from apps.calls import bus, writer
from apps.calls.models import Call, Event, Leg
from contracts import messages as m
from contracts.envelope import Command, encode
from contracts.envelope import Event as Message

pytestmark = pytest.mark.django_db

CALL, CUSTOMER, AGENT_LEG = "1791940259.3", "1791940259.3", "agent-1"


def a_call() -> list[Message]:
    return [
        Message(
            m.CALL_STARTED,
            {
                "call": CALL,
                "leg": CUSTOMER,
                "entry": "800",
                "queue": "Support",
                "caller": "lab-customer",
                "caller_name": "Customer",
                "sip_call_id": "abc@lab",
            },
            at="2026-10-14T04:10:59+00:00",
        ),
        Message(
            m.CALL_OFFERED,
            {"call": CALL, "leg": "agent-0", "agent": "a", "endpoint": "PJSIP/1001"},
            at="2026-10-14T04:11:00+00:00",
        ),
        Message(
            m.CALL_OFFER_FAILED,
            {"call": CALL, "leg": "agent-0", "agent": "a", "reason": "rejected", "cause": 17},
            at="2026-10-14T04:11:01+00:00",
        ),
        Message(
            m.CALL_OFFERED,
            {"call": CALL, "leg": AGENT_LEG, "agent": "b", "endpoint": "PJSIP/1002"},
            at="2026-10-14T04:11:02+00:00",
        ),
        Message(
            m.CALL_CONNECTED,
            {"call": CALL, "leg": AGENT_LEG, "agent": "b", "sip_call_id": "xyz"},
            at="2026-10-14T04:11:05+00:00",
        ),
        Message(
            m.LEG_ENDED, {"call": CALL, "leg": AGENT_LEG, "role": "agent"}, at="2026-10-14T04:12:00+00:00"
        ),
        Message(
            m.LEG_ENDED, {"call": CALL, "leg": CUSTOMER, "role": "customer"}, at="2026-10-14T04:12:00+00:00"
        ),
        Message(
            m.CALL_ENDED,
            {"call": CALL, "result": "answered", "reason": "customer hung up", "agent": "b"},
            at="2026-10-14T04:12:00+00:00",
        ),
    ]


def test_a_whole_call_becomes_one_call_with_its_legs():
    for message in a_call():
        assert writer.write(message)

    call = Call.objects.get(linkedid=CALL)
    assert (call.entry, call.queue, call.caller, call.result, call.agent_uid) == (
        "800",
        "Support",
        "lab-customer",
        "answered",
        "b",
    )
    assert call.answered_at.second == 5 and call.ended_at.minute == 12
    legs = {leg.uniqueid: leg for leg in Leg.objects.filter(call=call)}
    assert set(legs) == {CUSTOMER, "agent-0", AGENT_LEG}
    assert (legs["agent-0"].outcome, legs["agent-0"].cause) == ("rejected", 17)
    assert (legs[AGENT_LEG].outcome, legs[AGENT_LEG].sip_call_id) == ("answered", "xyz")
    assert legs[CUSTOMER].sip_call_id == "abc@lab" and legs[CUSTOMER].ended_at is not None
    assert Event.objects.filter(call_id=CALL).count() == 8


def test_an_event_delivered_twice_is_written_once():
    messages = a_call()
    for message in messages + messages:
        writer.write(message)
    assert Event.objects.count() == 8
    assert Leg.objects.count() == 3


def test_an_agent_leg_still_ringing_when_the_call_ends_is_cancelled():
    started, offered = a_call()[:2]
    for message in (
        started,
        offered,
        Message(m.LEG_ENDED, {"call": CALL, "leg": "agent-0", "role": "agent"}),
    ):
        writer.write(message)
    assert Leg.objects.get(uniqueid="agent-0").outcome == "cancelled"


def test_events_of_a_call_whose_start_was_missed_still_make_a_call():
    writer.write(Message(m.CALL_ENDED, {"call": "lost", "result": "answered", "reason": "x"}))
    assert Call.objects.get(linkedid="lost").result == "answered"


def test_unknown_event_types_are_kept_in_the_log():
    writer.write(Message("agent.something_new", {"agent": "a"}))
    assert Event.objects.get(type="agent.something_new").agent_uid == "a"


@pytest.fixture
def stream(settings):
    settings.BUS_EVENTS_STREAM = "test:writer:events"
    client = bus.client()
    client.delete(settings.BUS_EVENTS_STREAM)
    yield client
    client.delete(settings.BUS_EVENTS_STREAM)


def test_the_writer_reads_the_stream_acknowledges_and_skips_garbage(stream):
    for message in a_call():
        stream.xadd(settings.BUS_EVENTS_STREAM, {bus.FIELD: encode(message)})
    stream.xadd(settings.BUS_EVENTS_STREAM, {bus.FIELD: "not json"})
    stream.xadd(settings.BUS_EVENTS_STREAM, {bus.FIELD: encode(Command(m.AGENT_READY, {"agent": "a"}))})

    reader = bus.StreamReader(group=writer.GROUP, consumer="test")
    assert writer.drain(reader, pending=False, block_ms=100) == 10
    assert Event.objects.count() == 8
    assert stream.xpending(settings.BUS_EVENTS_STREAM, writer.GROUP)["pending"] == 0


def test_an_entry_that_failed_to_write_is_read_again(stream, monkeypatch):
    stream.xadd(settings.BUS_EVENTS_STREAM, {bus.FIELD: encode(a_call()[0])})
    reader = bus.StreamReader(group=writer.GROUP, consumer="test")

    def broken(message):
        raise RuntimeError("database went away")

    monkeypatch.setattr(writer, "write", broken)
    with pytest.raises(RuntimeError):
        writer.drain(reader, pending=False, block_ms=100)
    monkeypatch.undo()
    assert writer.drain(reader, pending=True) == 1  # the unacknowledged entry comes back
    assert Call.objects.filter(linkedid=CALL).exists()


def test_send_command_puts_a_checked_command_on_the_stream(settings):
    settings.BUS_COMMANDS_STREAM = "test:writer:commands"
    client = bus.client()
    client.delete(settings.BUS_COMMANDS_STREAM)
    command_id = bus.send_command(m.AGENT_READY, {"agent": "a"})
    [(_, fields)] = client.xrange(settings.BUS_COMMANDS_STREAM)
    assert command_id in fields[bus.FIELD.encode()].decode()
    with pytest.raises(Exception, match="agent"):
        bus.send_command(m.AGENT_READY, {})
    client.delete(settings.BUS_COMMANDS_STREAM)


def test_a_transfer_is_recorded_with_its_status():
    from apps.calls.models import Transfer

    for message in a_call()[:5]:
        writer.write(message)
    started = Message(
        m.TRANSFER_STARTED,
        {
            "call": CALL,
            "transfer": "t1",
            "kind": "consult",
            "target_kind": "agent",
            "target": "7003",
            "agent": "b",
            "target_agent": "c",
        },
    )
    answered = Message(m.TRANSFER_ANSWERED, {"call": CALL, "transfer": "t1", "leg": "agent-9"})
    done = Message(m.CONFERENCE_STARTED, {"call": CALL, "transfer": "t1", "legs": [], "agent": "b"})
    for message in (started, answered, done, started):
        writer.write(message)
    transfer = Transfer.objects.get(transfer_id="t1")
    assert (
        transfer.kind,
        transfer.target_kind,
        transfer.target,
        transfer.by_agent_uid,
        transfer.target_agent_uid,
    ) == ("consult", "agent", "7003", "b", "c")
    assert transfer.status == "conference" and transfer.answered_at and transfer.ended_at
    assert transfer.call.linkedid == CALL


def test_an_ai_call_shows_both_parts_of_the_interaction():
    """M8: the AI answered, handed off with a summary; an agent took it and wrapped it up."""
    from apps.calls.models import InteractionPart, PartKind

    call, ai_leg, agent_leg = "1791990100.1", "ai-1", "agent-7"
    transcript = [
        {"who": "ai", "text": "Habari", "at": 0.0},
        {"who": "caller", "text": "Nataka mtu", "at": 3.2},
    ]
    events = [
        (
            m.CALL_STARTED,
            {
                "call": call,
                "leg": call,
                "entry": "800",
                "queue": "General",
                "caller": "0712",
                "sip_call_id": "s",
            },
        ),
        (m.AI_STARTED, {"call": call, "leg": ai_leg, "ai_agent": "ai-uid", "language": "swahili"}),
        (m.AI_CONNECTED, {"call": call, "leg": ai_leg}),
        (
            m.AI_ENDED,
            {
                "call": call,
                "outcome": "handoff",
                "reason": "asked_for_person",
                "summary": "Wants a person.",
                "transcript": transcript,
            },
        ),
        (m.LEG_ENDED, {"call": call, "leg": ai_leg, "role": "ai"}),
        (
            m.CALL_OFFERED,
            {
                "call": call,
                "leg": agent_leg,
                "agent": "b",
                "endpoint": "PJSIP/1002",
                "summary": "Wants a person.",
            },
        ),
        (m.CALL_CONNECTED, {"call": call, "leg": agent_leg, "agent": "b"}),
        (m.CALL_ENDED, {"call": call, "result": "answered", "reason": "customer hung up", "agent": "b"}),
        (m.AGENT_WRAPPED_UP, {"agent": "b", "call": call, "code": "Information given", "by": "agent"}),
    ]
    for second, (kind, data) in enumerate(events):
        writer.write(Message(kind, data, at=f"2026-10-14T05:00:{second:02d}+00:00"))

    ai, agent = InteractionPart.objects.filter(call__linkedid=call)
    assert (ai.kind, ai.outcome, ai.reason, ai.summary) == (
        PartKind.AI,
        "handoff",
        "asked_for_person",
        "Wants a person.",
    )
    assert ai.transcript == transcript and ai.ai_agent_uid == "ai-uid" and ai.ended_at.second == 3
    assert (agent.kind, agent.agent_uid, agent.wrapup_code) == (PartKind.AGENT, "b", "Information given")
    assert agent.started_at.second == 6 and agent.ended_at.second == 7
    leg = Leg.objects.get(uniqueid=ai_leg)
    assert (leg.role, leg.outcome, leg.answered_at.second, leg.ended_at.second) == ("ai", "answered", 2, 4)


def test_a_call_the_ai_resolved():
    from apps.calls.models import InteractionPart

    call = "1791990100.2"
    for second, (kind, data) in enumerate(
        [
            (
                m.CALL_STARTED,
                {
                    "call": call,
                    "leg": call,
                    "entry": "802",
                    "queue": "General",
                    "caller": "0712",
                    "sip_call_id": "s",
                },
            ),
            (m.AI_STARTED, {"call": call, "leg": "ai-2", "ai_agent": "ai-uid"}),
            (
                m.AI_ENDED,
                {"call": call, "outcome": "resolved", "reason": "answered the question", "summary": "Hours."},
            ),
            (m.CALL_ENDED, {"call": call, "result": "ai_handled", "reason": "answered the question"}),
        ]
    ):
        writer.write(Message(kind, data, at=f"2026-10-14T05:01:{second:02d}+00:00"))
    assert Call.objects.get(linkedid=call).result == "ai_handled"
    [part] = InteractionPart.objects.filter(call__linkedid=call)
    assert (part.outcome, part.summary, part.transcript) == ("resolved", "Hours.", [])
