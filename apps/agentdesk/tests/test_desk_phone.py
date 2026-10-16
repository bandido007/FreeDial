"""An agent can take calls on a desk phone (a SOFTPHONE SIP account such as 1001) instead of the browser.

The admin sets the phone on the agent; an agent can only ever use the phone an admin gave them. At
sign-in the brain is told to ring that phone, no browser account is made, and Asterisk is asked
whether the phone is registered. Sign-out leaves the phone alone: it is a real phone.
"""

import json

import pytest
from django.conf import settings

from apps.agentdesk import desk
from apps.agents import services as agents
from apps.agents.models import Agent, AgentSession
from apps.calls import asterisk, bus
from apps.core.exceptions import ValidationError
from apps.telephony import services as telephony
from apps.telephony.models import PsAuth, SipAccount
from contracts import messages as m
from contracts.envelope import decode

pytestmark = pytest.mark.django_db

START = """mutation { startAgentSession { status code message data { sipId password wsUrl domain endpoint
  commandId deskPhone phoneState } } }"""
END = "mutation { endAgentSession { status code message data { commandId } } }"
PASSWORD = "Desk-phone-secret-1"


@pytest.fixture
def commands(settings):
    settings.BUS_COMMANDS_STREAM = "test:deskphone:commands"
    settings.BUS_AGENTS_KEY = "test:deskphone:agents"
    client = bus.client()
    client.delete(settings.BUS_COMMANDS_STREAM, settings.BUS_AGENTS_KEY)
    yield lambda: [
        decode(fields[bus.FIELD.encode()].decode())
        for _, fields in client.xrange(settings.BUS_COMMANDS_STREAM)
    ]
    client.delete(settings.BUS_COMMANDS_STREAM, settings.BUS_AGENTS_KEY)


@pytest.fixture
def phone():
    return telephony.create_sip_account(
        actor=None, sip_id="1001", kind="SOFTPHONE", password=PASSWORD, display_name="Laptop"
    )


@pytest.fixture
def online(monkeypatch):
    """What Asterisk says about each phone (ARI GET /endpoints/PJSIP/<id>)."""
    states = {"1001": "online"}
    monkeypatch.setattr(asterisk, "endpoint_state", lambda sip_id: states.get(sip_id, "offline"))
    return states


@pytest.fixture
def desk_agent(make_user, grant, phone):
    person = make_user(email="amina@example.org", account_type="Internal")
    grant(person, "can_take_calls")
    agents.create_agent(actor=None, number="2468", name="Amina", user=person, phone=phone)
    return person


def call(api_client, query):
    response = api_client.post("/graphql", data=json.dumps({"query": query}), content_type="application/json")
    body = json.loads(response.content)
    assert "errors" not in body, body
    return next(iter(body["data"].values()))


def test_only_a_softphone_can_be_an_agents_phone(phone):
    browser, _ = telephony.issue_browser_phone(actor=None, sip_id="web7001")
    with pytest.raises(ValidationError) as refused:
        agents.create_agent(actor=None, number="2469", name="Juma", phone=browser)
    assert refused.value.code == agents.NOT_A_DESK_PHONE
    agent = agents.create_agent(actor=None, number="2469", name="Juma", phone=phone)
    assert agent.phone == phone
    agents.update_agent(uid=agent.uid, actor=None, clear_phone=True)
    assert Agent.objects.get(pk=agent.pk).phone is None, "empty again: the browser phone"


def test_signing_in_rings_the_desk_phone_and_makes_no_browser_account(
    api, desk_agent, online, commands, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        session = call(api(desk_agent), START)["data"]
    assert (session["deskPhone"], session["sipId"], session["endpoint"]) == (True, "1001", "PJSIP/1001")
    assert session["password"] is None, "the desk phone's password stays in the phone"
    assert session["phoneState"] == "online"
    assert not SipAccount.objects.filter(sip_id="web2468").exists()

    sent = commands()[-1]
    assert (sent.type, sent.data) == (
        m.AGENT_LOGIN,
        {"agent": str(desk_agent.agent.uid), "endpoint": "PJSIP/1001"},
    )
    open_session = AgentSession.objects.get(agent=desk_agent.agent, logout_at=None)
    assert (open_session.endpoint, open_session.sip_account.sip_id) == ("PJSIP/1001", "1001")


def test_an_unregistered_phone_is_said_plainly(api, desk_agent, online, commands):
    online["1001"] = "offline"
    assert call(api(desk_agent), START)["data"]["phoneState"] == "offline"


def test_signing_out_leaves_the_desk_phone_working(
    api, desk_agent, online, commands, phone, django_capture_on_commit_callbacks
):
    client = api(desk_agent)
    digest = PsAuth.objects.get(id="1001").password_digest
    call(client, START)
    with django_capture_on_commit_callbacks(execute=True):
        assert call(client, END)["code"] == 9001
    phone.refresh_from_db()
    assert phone.active and PsAuth.objects.get(id="1001").password_digest == digest
    assert AgentSession.objects.get(agent=desk_agent.agent).logout_at is not None


def test_one_desk_phone_one_signed_in_agent(api, desk_agent, online, commands, make_user, grant, phone):
    """Two agents may share a desk across shifts, but not at the same time."""
    other = make_user(email="baraka@example.org", account_type="Internal")
    grant(other, "can_take_calls")
    agents.create_agent(actor=None, number="2470", name="Baraka", user=other, phone=phone)
    call(api(desk_agent), START)
    refused = call(api(other), START)
    assert refused["code"] == desk.PHONE_IN_USE and "2468 Amina" in refused["message"]
    call(api(desk_agent), END)
    assert call(api(other), START)["data"]["endpoint"] == "PJSIP/1001"


def test_a_switched_off_phone_cannot_be_used(api, desk_agent, online, commands, phone):
    telephony.set_sip_account_active(uid=phone.uid, actor=None, active=False)
    refused = call(api(desk_agent), START)
    assert refused["code"] == desk.PHONE_OFF and "1001" in refused["message"]


def test_asterisk_is_asked_about_the_phone(monkeypatch):
    seen = {}

    class Answer:
        status_code = 200

        def json(self):
            return {"technology": "PJSIP", "resource": "1001", "state": "online"}

        def raise_for_status(self):
            pass

    def fake_get(url, **kwargs):
        seen["url"] = url
        return Answer()

    monkeypatch.setattr(asterisk.requests, "get", fake_get)
    assert asterisk.endpoint_state("1001") == "online"
    assert seen["url"] == f"{settings.ARI_URL}/endpoints/PJSIP/1001"

    def down(url, **kwargs):
        raise asterisk.requests.ConnectionError("refused")

    monkeypatch.setattr(asterisk.requests, "get", down)
    assert asterisk.endpoint_state("1001") == "unknown"


def test_the_admin_sets_the_phone_on_the_agent(client, make_user, grant, phone):
    manager = make_user(account_type="Internal", is_staff=True)
    grant(manager, "can_view_agents", "can_manage_agents")
    client.force_login(manager)
    telephony.issue_browser_phone(actor=None, sip_id="web7001")
    form = client.get("/admin/agents/agent/add/").content.decode()
    assert ">1001 (SOFTPHONE)<" in form or ">1001" in form
    assert "web7001" not in form, "browser phones are never offered"
    response = client.post(
        "/admin/agents/agent/add/",
        {
            "number": "2471",
            "name": "Desk",
            "phone": str(phone.pk),
            "active": "on",
            "skills-TOTAL_FORMS": "0",
            "skills-INITIAL_FORMS": "0",
        },
    )
    assert response.status_code == 302, response.content.decode()[:2000]
    agent = Agent.objects.get(number="2471")
    assert agent.phone == phone
    assert "Desk phone 1001" in client.get("/admin/agents/agent/?table=1").content.decode()


def test_the_page_can_ask_for_the_phone_state(api, desk_agent, online, commands, make_user, grant):
    query = "query { myPhoneState { status code message data { state } } }"
    assert call(api(desk_agent), query)["data"]["state"] == "online"
    online["1001"] = "offline"
    assert call(api(desk_agent), query)["data"]["state"] == "offline"
    browser_agent = make_user(email="juma@example.org", account_type="Internal")
    grant(browser_agent, "can_take_calls")
    agents.create_agent(actor=None, number="2472", name="Juma", user=browser_agent)
    assert call(api(browser_agent), query)["data"]["state"] == "", "a browser phone reports itself"
