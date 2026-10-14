import json

import pytest

from contracts.envelope import VERSION, Command, ContractError, Event, decode, encode


def test_every_command_gets_a_command_id():
    first = Command(type="agent.set_state", data={"agent": "1001"})
    second = Command(type="agent.set_state", data={"agent": "1001"})
    assert first.command_id and second.command_id
    assert first.command_id != second.command_id


def test_every_event_gets_an_event_id():
    assert Event(type="call.started", data={}).event_id


def test_command_round_trip():
    command = Command(type="config.changed", data={"what": "queues"})
    assert decode(encode(command)) == command


def test_event_round_trip_keeps_the_causing_command():
    event = Event(type="agent.state_changed", data={"agent": "1001"}, command_id="c-1")
    decoded = decode(encode(event))
    assert decoded == event
    assert decoded.command_id == "c-1"


def test_encoded_message_names_its_kind_and_version():
    message = json.loads(encode(Command(type="config.changed", data={})))
    assert message["kind"] == "command"
    assert message["version"] == VERSION


@pytest.mark.parametrize(
    "message",
    [
        {"kind": "command", "version": VERSION, "type": "x", "data": {}, "sent_at": "t"},
        {"kind": "command", "version": VERSION, "type": "x", "data": {}, "sent_at": "t", "command_id": ""},
        {"kind": "event", "version": VERSION, "type": "x", "data": {}, "at": "t"},
    ],
    ids=["command without id", "command with empty id", "event without id"],
)
def test_a_message_without_its_id_is_refused(message):
    with pytest.raises(ContractError):
        decode(json.dumps(message))


def test_a_field_added_later_in_the_same_version_is_ignored():
    message = json.loads(encode(Command(type="config.changed", data={})))
    message["priority"] = "high"
    assert decode(json.dumps(message)).type == "config.changed"


def test_an_unknown_version_is_refused():
    message = json.loads(encode(Command(type="config.changed", data={})))
    message["version"] = VERSION + 1
    with pytest.raises(ContractError, match="version"):
        decode(json.dumps(message))


@pytest.mark.parametrize("text", ["not json", "[]", '{"kind": "gossip", "version": 1}'])
def test_garbage_is_refused(text):
    with pytest.raises(ContractError):
        decode(text)
