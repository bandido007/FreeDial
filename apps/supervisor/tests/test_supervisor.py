"""M6: the supervisor page's API and live socket: permissions per action, and only one's own teams."""

import asyncio
import json
from datetime import timedelta

import pytest
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import Client
from django.urls import path
from django.utils import timezone
from oauth2_provider.models import AccessToken

from apps.agents.models import Agent
from apps.calls import bus
from apps.supervisor.consumers import UNAUTHORIZED, SupervisorConsumer
from apps.teams import services as teams
from apps.telephony.models import PsEndpoint
from contracts import messages as m
from contracts.envelope import decode

pytestmark = pytest.mark.django_db


@pytest.fixture
def redis_keys(settings):
    settings.BUS_COMMANDS_STREAM = "test:sup:commands"
    settings.BUS_AGENTS_KEY = "test:sup:agents"
    settings.BUS_CALLS_KEY = "test:sup:calls"
    client = bus.client()
    keys = (settings.BUS_COMMANDS_STREAM, settings.BUS_AGENTS_KEY, settings.BUS_CALLS_KEY)
    client.delete(*keys)
    yield lambda: [
        decode(f[bus.FIELD.encode()].decode()) for _, f in client.xrange(settings.BUS_COMMANDS_STREAM)
    ]
    client.delete(*keys)


@pytest.fixture
def centre(make_user, grant, redis_keys):
    """Asha supervises Front Desk (Juma); Daudi's team has Halima. Juma is talking to a customer."""
    asha, daudi = make_user(email="asha@example.org"), make_user(email="daudi@example.org")
    juma_user, halima_user = make_user(email="juma@example.org"), make_user(email="halima@example.org")
    juma = Agent.objects.create(number="7101", name="Juma", user=juma_user)
    halima = Agent.objects.create(number="7105", name="Halima", user=halima_user)
    teams.create_team(actor=None, name="Front Desk", supervisors=[asha], agents=[juma_user])
    teams.create_team(actor=None, name="Claims", supervisors=[daudi], agents=[halima_user])
    client = bus.client()
    for call, agent in (("c-juma", juma), ("c-halima", halima)):
        snapshot = {
            "call": call,
            "queue": "Q",
            "state": "connected",
            "agent": str(agent.uid),
            "caller": "0712",
            "ringing": [],
        }
        client.hset(settings.BUS_CALLS_KEY, call, json.dumps(snapshot))
    return {"asha": asha, "daudi": daudi, "juma": juma, "halima": halima, "grant": grant}


def gql_as(api, user, query, variables=None):
    response = api(user).post(
        "/graphql",
        data=json.dumps({"query": query, "variables": variables or {}}),
        content_type="application/json",
    )
    return next(iter(json.loads(response.content)["data"].values()))


SUPERVISE = "mutation S($call: String!, $mode: String!) { superviseCall(call: $call, mode: $mode) { code } }"


def test_each_mode_needs_its_own_permission(api, centre, redis_keys):
    asha = centre["asha"]
    centre["grant"](asha, "can_monitor_calls")
    assert gql_as(api, asha, SUPERVISE, {"call": "c-juma", "mode": "monitor"})["code"] == 9001
    assert gql_as(api, asha, SUPERVISE, {"call": "c-juma", "mode": "barge"})["code"] == 9000
    [sent] = redis_keys()
    assert (sent.type, sent.data["mode"], sent.data["endpoint"], sent.data["supervisor"]) == (
        m.SUPERVISE_START,
        "monitor",
        f"PJSIP/websup{asha.pk}",
        str(asha.uid),
    )


def test_only_calls_of_ones_own_teams(api, centre, redis_keys):
    asha = centre["asha"]
    centre["grant"](asha, "can_monitor_calls", "can_whisper_calls", "can_barge_calls")
    assert gql_as(api, asha, SUPERVISE, {"call": "c-halima", "mode": "whisper"})["code"] == 10700
    assert gql_as(api, asha, SUPERVISE, {"call": "c-gone", "mode": "whisper"})["code"] == 10701
    assert redis_keys() == []


def test_forcing_an_agent_state_is_a_brain_command_marked_with_who(api, centre, redis_keys):
    asha = centre["asha"]
    force = "mutation F($a: String!, $s: String!, $r: String) { forceAgentState(agent: $a, state: $s, reason: $r) { code } }"
    assert gql_as(api, asha, force, {"a": str(centre["juma"].uid), "s": "ready"})["code"] == 9000, (
        "no permission yet"
    )
    centre["grant"](asha, "can_force_agent_state")
    assert (
        gql_as(api, asha, force, {"a": str(centre["juma"].uid), "s": "not_ready", "r": "Meeting"})["code"]
        == 9001
    )
    assert gql_as(api, asha, force, {"a": str(centre["halima"].uid), "s": "ready"})["code"] == 10700
    [sent] = redis_keys()
    assert (sent.type, sent.data) == (
        m.AGENT_NOT_READY,
        {"agent": str(centre["juma"].uid), "by": str(asha.uid), "reason": "Meeting"},
    )


def test_the_desk_shows_only_ones_teams(api, centre, redis_keys):
    asha = centre["asha"]
    centre["grant"](asha, "can_monitor_calls")
    desk = gql_as(api, asha, "query { mySupervisorDesk { code data { can calls agents { name } } } }")["data"]
    assert [agent["name"] for agent in desk["agents"]] == ["Juma"]
    assert [call["call"] for call in desk["calls"]] == ["c-juma"]
    assert desk["can"] == ["monitor"]


def test_the_supervisor_gets_a_browser_phone(api, centre, redis_keys):
    asha = centre["asha"]
    centre["grant"](asha, "can_monitor_calls")
    phone = gql_as(api, asha, "mutation { startSupervisorSession { code data { sipId password } } }")["data"]
    assert phone["sipId"] == f"websup{asha.pk}" and phone["password"]
    assert PsEndpoint.objects.get(id=phone["sipId"]).webrtc == "yes"


def test_the_page_is_served_with_the_microphone_allowed():
    page = Client().get("/supervisor/")
    assert page.status_code == 200 and "supervisor/app.js" in page.content.decode()
    assert "microphone=(self)" in page["Permissions-Policy"]


# ── the live socket ───────────────────────────────────────────────────────
@pytest.mark.django_db(transaction=True)
def test_the_live_socket_passes_on_only_ones_teams(make_user, grant, oauth_app, redis_keys):
    asha, juma_user, halima_user = make_user(email="asha@example.org"), make_user(), make_user()
    juma = Agent.objects.create(number="7101", name="Juma", user=juma_user)
    halima = Agent.objects.create(number="7105", name="Halima", user=halima_user)
    teams.create_team(actor=None, name="Front Desk", supervisors=[asha], agents=[juma_user])
    grant(asha, "can_monitor_calls")
    token = AccessToken.objects.create(
        user=asha,
        application=oauth_app,
        token="sup-token",
        scope="read write",
        expires=timezone.now() + timedelta(hours=1),
    )

    async def scenario():
        socket = WebsocketCommunicator(
            URLRouter([path("ws/supervisor", SupervisorConsumer.as_asgi())]), "/ws/supervisor"
        )
        assert (await socket.connect())[0]
        await socket.send_json_to({"type": "auth", "token": token.token})
        hello = await socket.receive_json_from(timeout=5)
        assert hello["type"] == "hello" and [a["name"] for a in hello["desk"]["agents"]] == ["Juma"]
        layer = get_channel_layer()
        for agent in (halima, juma):
            event = {"type": m.AGENT_STATE, "data": {"agent": str(agent.uid), "availability": "ready"}}
            await layer.group_send(bus.SUPERVISORS_GROUP, {"type": "brain.event", "event": event})
        mine = await socket.receive_json_from(timeout=5)
        assert mine["event"]["data"]["agent"] == str(juma.uid), "Halima's state never arrives"
        snapshot = {"call": "c1", "queue": "Q", "agent": str(juma.uid), "state": "connected", "ringing": []}
        await layer.group_send(
            bus.SUPERVISORS_GROUP, {"type": "live.call", "call": "c1", "snapshot": snapshot}
        )
        assert (await socket.receive_json_from(timeout=5))["snapshot"]["call"] == "c1"
        await layer.group_send(bus.SUPERVISORS_GROUP, {"type": "live.call", "call": "c1", "snapshot": None})
        assert (await socket.receive_json_from(timeout=5)) == {"type": "call", "call": "c1", "snapshot": None}
        await socket.disconnect()

    async def stranger():
        socket = WebsocketCommunicator(
            URLRouter([path("ws/supervisor", SupervisorConsumer.as_asgi())]), "/ws/supervisor"
        )
        assert (await socket.connect())[0]
        await socket.send_json_to({"type": "auth", "token": "nope"})
        assert (await socket.receive_output(timeout=5))["code"] == UNAUTHORIZED

    asyncio.run(scenario())
    asyncio.run(stranger())
    from channels.db import database_sync_to_async
    from django.db import connections

    asyncio.run(database_sync_to_async(connections.close_all)())


def test_supervisor_receives_team_filter_state_timestamps_and_reason_limits(api, centre, redis_keys):
    from uuid import uuid4

    from apps.agents.models import NotReadyReason
    from apps.calls.models import Event

    centre["grant"](centre["asha"], "can_monitor_calls")
    NotReadyReason.objects.create(name="Break", max_seconds=900)
    uid = str(centre["juma"].uid)
    Event.objects.create(
        event_id=uuid4(),
        type=m.AGENT_STATE,
        at=timezone.now(),
        agent_uid=uid,
        data={"availability": "ready", "activity": "talking", "call": "c-juma"},
    )
    bus.client().hset(
        settings.BUS_AGENTS_KEY,
        uid,
        json.dumps({"availability": "ready", "activity": "talking", "call": "c-juma"}),
    )
    data = gql_as(
        api,
        centre["asha"],
        """query { mySupervisorDesk { data {
      teams reasonLimits longCallSeconds agents { name stateSince }
    } } }""",
    )["data"]
    assert [team["name"] for team in data["teams"]] == ["Front Desk"]
    assert data["reasonLimits"]["Break"] == 900 and data["longCallSeconds"] == 600
    assert data["agents"][0]["stateSince"] is not None


def test_history_is_team_scoped_and_does_not_expose_event_payloads(api, centre, redis_keys):
    from uuid import uuid4

    from apps.calls.models import Event

    asha = centre["asha"]
    centre["grant"](asha, "can_monitor_calls")
    Event.objects.create(
        event_id=uuid4(),
        type=m.AGENT_STATE,
        at=timezone.now(),
        agent_uid=str(centre["juma"].uid),
        data={
            "availability": "not_ready",
            "activity": "idle",
            "reason": "Break",
            "notes": "private caller notes",
        },
    )
    query = "query H($a: String!) { supervisedAgentHistory(agent: $a) { code dataList { description } } }"
    own = gql_as(api, asha, query, {"a": str(centre["juma"].uid)})
    assert own["code"] == 9001 and "Break" in own["dataList"][0]["description"]
    assert "private caller notes" not in str(own)
    assert gql_as(api, asha, query, {"a": str(centre["halima"].uid)})["code"] == 10700


def test_force_not_ready_requires_a_configured_reason(api, centre, redis_keys):
    from apps.agents.models import NotReadyReason

    asha = centre["asha"]
    centre["grant"](asha, "can_force_agent_state")
    NotReadyReason.objects.create(name="Break")
    query = 'mutation F($a: String!, $r: String) { forceAgentState(agent: $a, state: "not_ready", reason: $r) { code } }'
    for reason in ("", "Unconfigured reason"):
        assert gql_as(api, asha, query, {"a": str(centre["juma"].uid), "r": reason})["code"] == 10702
    assert redis_keys() == []


# ── "How my team is doing": the supervisor page's measurements ──────────────
MEASURES = """query M($period: String, $team: String) { myTeamMeasures(period: $period, team: $team) {
  status code message data { since team { answered missed medianAnswerSeconds overLimit actedOn }
  agents { uid number name answered missed } } } }"""


def test_a_supervisor_sees_measures_for_their_own_team_only(api, make_user, grant):
    import json
    import uuid

    from django.utils import timezone

    from apps.agents.models import Agent
    from apps.calls.models import Event
    from apps.teams import services as teams

    boss = make_user(email="boss@example.org", account_type="Internal")
    grant(boss, "can_monitor_calls")
    mine = make_user(email="mine@example.org", account_type="Internal")
    theirs = make_user(email="theirs@example.org", account_type="Internal")
    my_agent = Agent.objects.create(number="2001", name="Mine", user=mine)
    other = Agent.objects.create(number="2002", name="Theirs", user=theirs)
    team = teams.create_team(actor=None, name="Front", supervisors=[boss], agents=[mine])
    now = timezone.now()
    for agent, seconds in ((my_agent, 5), (other, 9)):
        Event.objects.create(
            event_id=uuid.uuid4(), type="offer.answered", at=now, agent_uid=str(agent.uid),
            data={"agent": str(agent.uid), "seconds": seconds},
        )  # fmt: skip

    def ask(**variables):
        response = api(boss).post(
            "/graphql",
            data=json.dumps({"query": MEASURES, "variables": variables}),
            content_type="application/json",
        )
        return json.loads(response.content)["data"]["myTeamMeasures"]

    answer = ask(period="today")
    assert answer["code"] == 9001, answer
    assert answer["data"]["team"]["answered"] == 1 and answer["data"]["team"]["medianAnswerSeconds"] == 5.0
    assert [line["name"] for line in answer["data"]["agents"]] == ["Mine"]
    assert ask(period="today", team=str(team.uid))["data"]["team"]["answered"] == 1
    refused = ask(period="today", team=str(uuid.uuid4()))
    assert refused["code"] != 9001, "a team that is not yours"
