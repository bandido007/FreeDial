"""UX_PRINCIPLES.md section 6: the agent page's measurement signals, and the 2-minute wrap-up correction.

Signals are measurement, never state: the web puts them straight on the events stream (``ux.*``),
stamped with the signed-in agent (never what the browser claims), and the writer stores them once.
"""

import json
import uuid

import pytest

from apps.agentdesk.tests.test_desk import call, commands  # noqa: F401 (fixture)
from apps.agents.models import Agent, WrapupCode
from apps.calls import bus, writer
from apps.calls.models import Call, Event, InteractionPart, PartKind
from contracts import messages as m
from contracts.envelope import Event as Message
from contracts.envelope import decode

pytestmark = pytest.mark.django_db

SIGNAL = """mutation S($signal: String!, $signalId: String!, $call: String, $data: GenericScalar) {
  recordUxSignal(signal: $signal, signalId: $signalId, call: $call, data: $data) { status code message } }"""


@pytest.fixture
def events(settings):
    settings.BUS_EVENTS_STREAM = "test:ux:events"
    client = bus.client()
    client.delete(settings.BUS_EVENTS_STREAM)
    yield lambda: [
        decode(fields[bus.FIELD.encode()].decode()) for _, fields in client.xrange(settings.BUS_EVENTS_STREAM)
    ]
    client.delete(settings.BUS_EVENTS_STREAM)


@pytest.fixture
def agent_user(make_user, grant):
    person = make_user(email="juma@example.org", account_type="Internal")
    grant(person, "can_take_calls")
    Agent.objects.create(number="7101", name="Juma", user=person)
    return person


def test_a_signal_is_stamped_with_the_signed_in_agent(api, agent_user, events):
    signal_id = str(uuid.uuid4())
    answer = call(
        api(agent_user),
        SIGNAL,
        {
            "signal": m.UX_OFFER_SHOWN,
            "signalId": signal_id,
            "call": "1792000000.1",
            "data": {"shown_at": "2026-10-15T08:00:00.120Z", "agent": "someone-else"},
        },
    )
    assert answer["code"] == 9001
    [event] = events()
    assert (event.type, event.event_id) == (m.UX_OFFER_SHOWN, signal_id)
    assert event.data == {
        "agent": str(agent_user.agent.uid),  # the browser cannot speak for another agent
        "call": "1792000000.1",
        "shown_at": "2026-10-15T08:00:00.120Z",
    }


def test_only_known_signals_and_fields_pass(api, agent_user, events):
    client = api(agent_user)
    bad = call(client, SIGNAL, {"signal": "agent.state", "signalId": str(uuid.uuid4()), "data": {}})
    assert bad["status"] is False, "a page cannot write state events"
    noisy = call(
        client,
        SIGNAL,
        {
            "signal": m.UX_ACTION,
            "signalId": str(uuid.uuid4()),
            "data": {"action": "hold", "ms": 180, "ok": True, "x": "y" * 9000},
        },
    )
    assert noisy["code"] == 9001
    assert "x" not in events()[-1].data, "unknown fields are dropped"
    not_an_id = call(client, SIGNAL, {"signal": m.UX_ACTION, "signalId": "1", "data": {}})
    assert not_an_id["status"] is False


def test_the_writer_stores_a_repeated_signal_once(api, agent_user, events):
    signal_id = str(uuid.uuid4())
    variables = {"signal": m.UX_AI_SUMMARY_WRONG, "signalId": signal_id, "call": "c1", "data": {}}
    call(api(agent_user), SIGNAL, variables)
    call(api(agent_user), SIGNAL, variables)  # a retry after a dropped connection
    for event in events():
        writer.write(event)
    assert Event.objects.filter(event_id=signal_id).count() == 1


def test_the_wrapup_code_can_be_corrected(api, agent_user, commands):  # noqa: F811
    WrapupCode.objects.create(name="Sale")
    WrapupCode.objects.create(name="Complaint")
    client = api(agent_user)
    mutation = 'mutation { changeMyWrapupCode(call: "c1", code: "Complaint") { code } }'
    assert call(client, mutation)["code"] == 9001
    assert (commands()[-1].type, commands()[-1].data) == (
        m.AGENT_WRAPUP_CODE_CHANGE,
        {"agent": str(agent_user.agent.uid), "call": "c1", "code": "Complaint"},
    )
    assert (
        call(client, 'mutation { changeMyWrapupCode(call: "c1", code: "Nonsense") { code } }')["code"]
        == 10206
    )


def test_the_writer_applies_a_corrected_code_to_the_call():
    call_row = Call.objects.create(linkedid="c9")
    part = InteractionPart.objects.create(
        call=call_row,
        kind=PartKind.AGENT,
        agent_uid="a1",
        started_at="2026-10-15T08:00:00Z",
        wrapup_code="Sale",
    )
    data = {"agent": "a1", "call": "c9", "code": "Complaint", "previous": "Sale"}
    writer.write(Message(m.WRAPUP_CODE_CHANGED, data, at="2026-10-15T08:01:00+00:00"))
    part.refresh_from_db()
    assert part.wrapup_code == "Complaint"


def test_a_refused_admin_form_is_measured(admin, client, events):
    client.force_login(admin)
    response = client.post("/admin/agents/skill/add/", {"name": "", "kind": "topic"})
    assert response.status_code == 200, "the form comes back with its errors"
    [event] = [e for e in events() if e.type == m.UX_ADMIN_VALIDATION_ERROR]
    assert event.data["model"] == "agents.skill" and "name" in event.data["fields"]
    assert json.dumps(event.data)  # plain data only: no values the admin typed
