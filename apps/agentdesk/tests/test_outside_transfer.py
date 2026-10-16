"""M4b: before a transfer to an outside number reaches the brain, the web checks a route allows it and
the agent holds that route's permission (can_dial_external), and says so plainly if not."""

import json

import pytest

from apps.agents.models import Agent
from apps.calls import bus
from apps.telephony import trunks
from contracts import messages as m
from contracts.envelope import decode

pytestmark = pytest.mark.django_db

TRANSFER = """mutation T($target: String!) { transferMyCall(kind: "blind", targetKind: "number", target: $target)
  { status code message } }"""


@pytest.fixture
def commands(settings):
    settings.BUS_COMMANDS_STREAM = "test:outside:commands"
    client = bus.client()
    client.delete(settings.BUS_COMMANDS_STREAM)
    yield lambda: [
        decode(fields[bus.FIELD.encode()].decode())
        for _, fields in client.xrange(settings.BUS_COMMANDS_STREAM)
    ]
    client.delete(settings.BUS_COMMANDS_STREAM)


@pytest.fixture(autouse=True)
def mobiles(monkeypatch):
    monkeypatch.setattr(trunks, "reload_registrations", lambda: None)
    trunk = trunks.save_trunk(actor=None, name="lab-carrier", preset="lab-sipp")
    trunks.save_route(actor=None, name="Mobiles", patterns="+2557", trunks=[trunk])


def agent(make_user, grant, *codes):
    person = make_user(account_type="Internal")
    grant(person, "can_take_calls", *codes)
    Agent.objects.create(number="7001", name="Asha", user=person)
    return person


def transfer(api_client, target):
    response = api_client.post(
        "/graphql",
        data=json.dumps({"query": TRANSFER, "variables": {"target": target}}),
        content_type="application/json",
    )
    return json.loads(response.content)["data"]["transferMyCall"]


def test_an_allowed_number_goes_to_the_brain(api, make_user, grant, commands):
    answer = transfer(api(agent(make_user, grant, "can_dial_external")), "0712345678")
    assert answer["code"] == 9001
    assert commands()[-1].type == m.CALL_TRANSFER


def test_a_number_no_route_allows_is_refused_at_once(api, make_user, grant, commands):
    answer = transfer(api(agent(make_user, grant, "can_dial_external")), "+254712345678")
    assert answer["code"] != 9001 and "+254712345678 cannot be called" in answer["message"]
    assert not commands()


def test_an_agent_without_the_permission_is_refused(api, make_user, grant, commands):
    answer = transfer(api(agent(make_user, grant)), "0712345678")
    assert "not allowed to call outside numbers" in answer["message"]
    assert not commands()


def test_an_internal_number_needs_no_route(api, make_user, grant, commands):
    assert transfer(api(agent(make_user, grant)), "1002")["code"] == 9001
