"""The supervisor page's live WebSocket (``/ws/supervisor``): brain events and live calls of one's teams.

Same protocol as the agent socket: the first message carries the bearer token. The relay
sends every event and every live-call change to all supervisor sockets; each socket passes
on only what concerns its supervisor's teams.
"""

import asyncio

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from oauth2_provider.models import AccessToken

from apps.authentication import gates
from apps.authentication.scopes import FIRST_PARTY_SCOPES
from apps.calls.bus import SUPERVISORS_GROUP
from apps.supervisor import desk

AUTH_TIMEOUT = 10
UNAUTHORIZED = 4401


def supervisor_for_token(token: str):
    if not token:
        return None
    access = AccessToken.objects.select_related("user").filter(token=token).first()
    if access is None or access.is_expired() or access.user is None:
        return None
    if not set(access.scope.split()) & FIRST_PARTY_SCOPES:
        return None
    user = access.user
    if gates.blocked_reason(user) is not None or not desk.may_supervise(user):
        return None
    return user


class SupervisorConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.user = None
        self.scope_rules = None
        self.calls_shown: set[str] = set()
        await self.accept()
        self.deadline = asyncio.get_running_loop().call_later(AUTH_TIMEOUT, self._too_slow)

    def _too_slow(self):
        if self.user is None:
            asyncio.ensure_future(self.close(code=UNAUTHORIZED))

    async def receive_json(self, content, **kwargs):
        if self.user is not None:
            return
        if not isinstance(content, dict) or content.get("type") != "auth":
            await self.close(code=UNAUTHORIZED)
            return
        user = await database_sync_to_async(supervisor_for_token)(str(content.get("token") or ""))
        if user is None:
            await self.close(code=UNAUTHORIZED)
            return
        self.user = user
        self.deadline.cancel()
        self.scope_rules = await database_sync_to_async(desk.scope_for)(user)
        await self.channel_layer.group_add(SUPERVISORS_GROUP, self.channel_name)
        state = await database_sync_to_async(desk.desk)(user)
        self.calls_shown = {call["call"] for call in state["calls"]}
        await self.send_json({"type": "hello", "desk": state})

    async def disconnect(self, code):
        if getattr(self, "deadline", None) is not None:
            self.deadline.cancel()
        if self.user is not None:
            await self.channel_layer.group_discard(SUPERVISORS_GROUP, self.channel_name)

    async def brain_event(self, message):
        event = message["event"]
        data = event.get("data") or {}
        if self.scope_rules is None:
            return
        if data.get("call") in self.calls_shown or self.scope_rules.has_event(data):
            await self.send_json({"type": "event", "event": event})

    async def live_call(self, message):
        call, snapshot = message["call"], message["snapshot"]
        if self.scope_rules is None:
            return
        if snapshot is not None and self.scope_rules.has_call(snapshot):
            self.calls_shown.add(call)
            await self.send_json({"type": "call", "call": call, "snapshot": snapshot})
        elif call in self.calls_shown:
            if snapshot is None:
                self.calls_shown.discard(call)
            await self.send_json({"type": "call", "call": call, "snapshot": snapshot})
