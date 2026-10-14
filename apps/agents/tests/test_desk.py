"""M3: what the agent page asks of the API, signed in with a real bearer token.

Every button becomes a command to the brain (rule 2); the browser phone gets its own
account with a password that lives for one session.
"""

import hashlib
import json

import pytest
from django.conf import settings

from apps.agents import desk
from apps.agents.models import Agent, AgentSession, NotReadyReason, WrapupCode
from apps.calls import bus
from apps.telephony.models import PsAuth, PsEndpoint, SipAccount
from contracts import messages as m
from contracts.envelope import decode

pytestmark = pytest.mark.django_db

DESK = """query { myAgentDesk { status code message data { uid number name notReadyReasons wrapupCodes
  state { availability activity reason call wrapupCall endpoint } } } }"""
START = "mutation { startAgentSession { status code message data { sipId password wsUrl domain endpoint commandId } } }"
END = "mutation { endAgentSession { status code message data { commandId } } }"


@pytest.fixture
def commands(settings):
    settings.BUS_COMMANDS_STREAM = "test:desk:commands"
    settings.BUS_AGENTS_KEY = "test:desk:agents"
    client = bus.client()
    client.delete(settings.BUS_COMMANDS_STREAM, settings.BUS_AGENTS_KEY)
    yield lambda: [
        decode(fields[bus.FIELD.encode()].decode())
        for _, fields in client.xrange(settings.BUS_COMMANDS_STREAM)
    ]
    client.delete(settings.BUS_COMMANDS_STREAM, settings.BUS_AGENTS_KEY)


@pytest.fixture
def agent_user(make_user, grant):
    person = make_user(email="asha@example.org", account_type="Internal")
    grant(person, "can_take_calls")
    Agent.objects.create(number="7001", name="Asha", user=person)
    return person


def call(api_client, query, variables=None):
    response = api_client.post(
        "/graphql",
        data=json.dumps({"query": query, "variables": variables or {}}),
        content_type="application/json",
    )
    body = json.loads(response.content)
    assert "errors" not in body, body
    return next(iter(body["data"].values()))


def md5_digest(user, password):
    return "MD5:" + hashlib.md5(f"{user}:{settings.PJSIP_REALM}:{password}".encode()).hexdigest()  # noqa: S324


def test_the_desk_shows_the_agent_their_state_and_their_choices(api, agent_user, commands):
    NotReadyReason.objects.create(name="Lunch")
    WrapupCode.objects.create(name="Sale")
    answer = call(api(agent_user), DESK)
    assert answer["code"] == 9001
    data = answer["data"]
    assert (data["number"], data["name"]) == ("7001", "Asha")
    assert data["state"]["availability"] == "logged_out"
    assert (data["notReadyReasons"], data["wrapupCodes"]) == (["Lunch"], ["Sale"])


def test_the_desk_reads_the_state_the_brain_saved(api, agent_user, commands):
    uid = str(agent_user.agent.uid)
    saved = {
        "agent": uid,
        "availability": "ready",
        "activity": "talking",
        "reason": "",
        "call": "c1",
        "endpoint": "PJSIP/web7001",
    }
    bus.client().hset(settings.BUS_AGENTS_KEY, uid, json.dumps(saved))
    state = call(api(agent_user), DESK)["data"]["state"]
    assert (state["availability"], state["activity"], state["call"]) == ("ready", "talking", "c1")


def test_starting_a_session_gives_a_webrtc_account_with_a_password_for_this_session(
    api, agent_user, commands, django_capture_on_commit_callbacks
):
    client = api(agent_user)
    with django_capture_on_commit_callbacks(execute=True):
        phone = call(client, START)["data"]
    assert (phone["sipId"], phone["endpoint"]) == ("web7001", "PJSIP/web7001")
    assert phone["wsUrl"] == settings.PJSIP_WS_URL

    endpoint = PsEndpoint.objects.get(id="web7001")
    assert endpoint.webrtc == "yes" and endpoint.transport == settings.PJSIP_TRANSPORT_BROWSER
    assert endpoint.allow == "ulaw,alaw"
    assert PsAuth.objects.get(id="web7001").password_digest == md5_digest("web7001", phone["password"])

    sent = commands()[-1]
    assert (sent.type, sent.data, sent.command_id) == (
        m.AGENT_LOGIN,
        {"agent": str(agent_user.agent.uid), "endpoint": "PJSIP/web7001"},
        phone["commandId"],
    )
    assert AgentSession.objects.get(agent=agent_user.agent, logout_at=None).endpoint == "PJSIP/web7001"

    again = call(client, START)["data"]
    assert again["password"] != phone["password"], "every session gets a new password"
    assert PsAuth.objects.get(id="web7001").password_digest == md5_digest("web7001", again["password"])
    assert AgentSession.objects.filter(agent=agent_user.agent, logout_at=None).count() == 1


def test_ending_the_session_logs_out_and_switches_the_phone_off(
    api, agent_user, commands, django_capture_on_commit_callbacks
):
    client = api(agent_user)
    call(client, START)
    with django_capture_on_commit_callbacks(execute=True):
        assert call(client, END)["code"] == 9001
    assert commands()[-1].type == m.AGENT_LOGOUT
    assert not PsEndpoint.objects.filter(id="web7001").exists(), (
        "the password is useless until the next session"
    )
    assert not SipAccount.objects.get(sip_id="web7001").active
    assert AgentSession.objects.get(agent=agent_user.agent).logout_at is not None


def test_signing_out_during_a_call_is_refused(api, agent_user, commands):
    client = api(agent_user)
    call(client, START)
    uid = str(agent_user.agent.uid)
    bus.client().hset(
        settings.BUS_AGENTS_KEY, uid, json.dumps({"agent": uid, "call": "c1", "activity": "talking"})
    )
    assert call(client, END)["code"] == desk.ON_A_CALL


def test_buttons_become_commands_for_the_brain(api, agent_user, commands):
    NotReadyReason.objects.create(name="Lunch")
    WrapupCode.objects.create(name="Sale")
    client = api(agent_user)
    uid = str(agent_user.agent.uid)

    call(client, "mutation { setMyAvailability(ready: true) { code } }")
    call(client, 'mutation { setMyAvailability(ready: false, reason: "Lunch") { code } }')
    call(client, "mutation { holdMyCall(hold: true) { code } }")
    call(client, "mutation { holdMyCall(hold: false) { code } }")
    call(client, 'mutation { finishMyWrapup(code: "Sale") { code data { commandId } } }')

    assert [(c.type, c.data) for c in commands()] == [
        (m.AGENT_READY, {"agent": uid}),
        (m.AGENT_NOT_READY, {"agent": uid, "reason": "Lunch"}),
        (m.CALL_HOLD, {"agent": uid}),
        (m.CALL_RESUME, {"agent": uid}),
        (m.AGENT_WRAPUP_DONE, {"agent": uid, "code": "Sale"}),
    ]


def test_reasons_and_codes_must_be_listed_ones(api, agent_user, commands):
    NotReadyReason.objects.create(name="Lunch")
    WrapupCode.objects.create(name="Sale")
    client = api(agent_user)
    assert (
        call(client, 'mutation { setMyAvailability(ready: false, reason: "Nap") { code } }')["code"] == 10206
    )
    assert call(client, 'mutation { finishMyWrapup(code: "Whatever") { code } }')["code"] == 10206
    assert commands() == []


def test_only_agents_with_the_permission_get_in(api, make_user, grant, commands):
    stranger = make_user(email="stranger@example.org", account_type="Internal")
    assert call(api(stranger), DESK)["code"] == 9000  # no can_take_calls

    no_agent = make_user(email="noagent@example.org", account_type="Internal")
    grant(no_agent, "can_take_calls")
    assert call(api(no_agent), DESK)["code"] == desk.NOT_AN_AGENT
    assert call(api(no_agent), START)["code"] == desk.NOT_AN_AGENT
