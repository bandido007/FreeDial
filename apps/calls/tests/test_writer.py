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
