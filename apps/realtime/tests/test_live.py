"""M3: the agent page's live WebSocket, opened with the same bearer token as the API."""

import asyncio
from datetime import timedelta

import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.utils import timezone
from oauth2_provider.models import AccessToken

from apps.agents.models import Agent
from apps.realtime import consumers, relay
from apps.realtime.routing import websocket_urlpatterns
from contracts import messages as m
from contracts.envelope import Event

pytestmark = pytest.mark.django_db(transaction=True)  # the consumer reads the database from its own thread


@pytest.fixture(autouse=True)
def close_consumer_connections():
    """The consumer's database work runs in a worker thread; close its connection after each test."""
    yield
    from channels.db import database_sync_to_async
    from django.db import connections

    asyncio.run(database_sync_to_async(connections.close_all)())


def application():
    from channels.routing import URLRouter

    return URLRouter(websocket_urlpatterns)


@pytest.fixture
def agent_token(make_user, grant, oauth_app):
    def make(*, scope="read write", permission=True, agent=True, expires_in=3600):
        person = make_user(account_type="Internal")
        if permission:
            grant(person, "can_take_calls")
        if agent:
            Agent.objects.create(number=f"70{person.pk:02d}", name="Asha", user=person)
        token = AccessToken.objects.create(
            user=person,
            application=oauth_app,
            token=f"token-{person.pk}-{scope.replace(' ', '-')}",
            expires=timezone.now() + timedelta(seconds=expires_in),
            scope=scope,
        )
        return token.token, person

    return make


async def open_socket(token):
    socket = WebsocketCommunicator(application(), "/ws/agent")
    connected, _ = await socket.connect()
    assert connected
    await socket.send_json_to({"type": "auth", "token": token})
    return socket


def test_a_valid_token_gets_hello_with_the_state_then_the_agents_events(agent_token):
    token, person = agent_token()
    uid = str(person.agent.uid)

    async def scenario():
        socket = await open_socket(token)
        hello = await socket.receive_json_from(timeout=5)
        assert hello["type"] == "hello" and hello["agent"]["uid"] == uid
        assert hello["state"]["availability"] == "logged_out"

        mine = Event(type=m.AGENT_STATE, data={"agent": uid, "availability": "ready"})
        other = Event(type=m.AGENT_STATE, data={"agent": "someone-else", "availability": "ready"})
        layer = get_channel_layer()
        for event in (other, mine):
            for group in relay.agents_concerned(event):
                payload = {"type": event.type, "data": event.data, "at": event.at, "event_id": event.event_id}
                await layer.group_send(
                    consumers.agent_group(group), {"type": "brain.event", "event": payload}
                )
        pushed = await socket.receive_json_from(timeout=5)
        assert pushed["event"]["data"] == {"agent": uid, "availability": "ready"}
        assert await socket.receive_nothing(timeout=0.2), "another agent's event never arrives"
        await socket.disconnect()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind",
    ["garbage", "expired", "identity_only", "no_permission", "not_an_agent"],
)
def test_anything_but_a_valid_agent_token_is_closed(agent_token, kind):
    tokens = {
        "garbage": lambda: ("not-a-token", None),
        "expired": lambda: agent_token(expires_in=-10),
        "identity_only": lambda: agent_token(scope="openid profile email"),
        "no_permission": lambda: agent_token(permission=False),
        "not_an_agent": lambda: agent_token(agent=False),
    }
    token, _ = tokens[kind]()

    async def scenario():
        socket = await open_socket(token)
        closed = await socket.receive_output(timeout=5)
        assert closed == {"type": "websocket.close", "code": consumers.UNAUTHORIZED}

    asyncio.run(scenario())


def test_a_socket_that_never_signs_in_is_closed(monkeypatch):
    monkeypatch.setattr(consumers, "AUTH_TIMEOUT", 0.1)

    async def scenario():
        socket = WebsocketCommunicator(application(), "/ws/agent")
        assert (await socket.connect())[0]
        closed = await socket.receive_output(timeout=3)
        assert closed["code"] == consumers.UNAUTHORIZED

    asyncio.run(scenario())


def test_the_relay_sends_each_event_to_the_agent_it_names():
    sent = []

    class Layer:
        async def group_send(self, group, message):
            sent.append((group, message["event"]["type"]))

    assert relay.push(Layer(), Event(type=m.CALL_OFFERED, data={"agent": "a1", "call": "c"})) == 1
    assert relay.push(Layer(), Event(type=m.CALL_STARTED, data={"call": "c"})) == 0
    assert sent == [
        ("agent.a1", m.CALL_OFFERED),
        ("supervisors", m.CALL_OFFERED),  # every supervisor page gets it and keeps what concerns its teams
        ("supervisors", m.CALL_STARTED),
    ]


def test_queue_invalidation_uses_the_same_authenticated_socket_without_caller_data(agent_token):
    token, _ = agent_token()

    async def scenario():
        socket = await open_socket(token)
        await socket.receive_json_from(timeout=5)
        await get_channel_layer().group_send(consumers.AGENT_DESKS_GROUP, {"type": "queues.changed"})
        assert await socket.receive_json_from(timeout=5) == {"type": "queues.changed"}
        await socket.disconnect()

    asyncio.run(scenario())
