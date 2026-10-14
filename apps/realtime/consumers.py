"""The agent page's live WebSocket (``/ws/agent``).

Browsers cannot put an Authorization header on a WebSocket, and a token in the
URL ends up in logs, so the page's first message carries it::

    -> {"type": "auth", "token": "<access token>"}
    <- {"type": "hello", "agent": {...}, "state": {...}}     (else closed with 4401)
    <- {"type": "event", "event": {"type": "agent.state", "data": {...}, "at": "..."}}

Nothing that must not be lost travels here: a page that reconnects says hello again
and gets the current state (CLAUDE.md, "Messaging").
"""

import asyncio

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from apps.agents import desk
from apps.realtime.auth import agent_for_token

AUTH_TIMEOUT = 10  # seconds a connection may stay open without a valid token
UNAUTHORIZED = 4401


def agent_group(uid: str) -> str:
    return f"agent.{uid}"


class AgentConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.agent = None
        self.group = None
        await self.accept()
        self.deadline = asyncio.get_running_loop().call_later(AUTH_TIMEOUT, self._too_slow)

    def _too_slow(self):
        if self.agent is None:
            asyncio.ensure_future(self.close(code=UNAUTHORIZED))

    async def receive_json(self, content, **kwargs):
        if self.agent is not None:
            return  # the page only listens; its buttons go through the API
        if not isinstance(content, dict) or content.get("type") != "auth":
            await self.close(code=UNAUTHORIZED)
            return
        agent = await database_sync_to_async(agent_for_token)(str(content.get("token") or ""))
        if agent is None:
            await self.close(code=UNAUTHORIZED)
            return
        self.agent = agent
        self.deadline.cancel()
        self.group = agent_group(str(agent.uid))
        await self.channel_layer.group_add(self.group, self.channel_name)
        state = await database_sync_to_async(desk.live_state)(agent)
        await self.send_json(
            {
                "type": "hello",
                "agent": {"uid": str(agent.uid), "number": agent.number, "name": agent.name},
                "state": state,
            }
        )

    async def disconnect(self, code):
        if getattr(self, "deadline", None) is not None:
            self.deadline.cancel()
        if self.group:
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def brain_event(self, message):
        await self.send_json({"type": "event", "event": message["event"]})
