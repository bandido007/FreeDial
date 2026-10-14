"""An agent's live state as the brain last saved it (Redis), for the web to read. Never written here (rule 2)."""

import json
from typing import Any

from django.conf import settings

from apps.agents.models import Agent
from apps.calls import bus

LOGGED_OUT = {
    "availability": "logged_out",
    "activity": "idle",
    "reason": "",
    "call": None,
    "wrapup_call": None,
    "endpoint": None,
}


def live_state(agent: Agent) -> dict[str, Any]:
    """The agent's state as the brain last saved it (Redis), or logged out."""
    raw = bus.client().hget(settings.BUS_AGENTS_KEY, str(agent.uid))
    return {**LOGGED_OUT, **json.loads(raw)} if isinstance(raw, (str, bytes)) else dict(LOGGED_OUT)


def live_states() -> dict[str, dict[str, Any]]:
    """Every agent's saved state, by agent uid."""
    saved = bus.client().hgetall(settings.BUS_AGENTS_KEY)
    out = {}
    for uid, raw in saved.items() if isinstance(saved, dict) else []:
        key = uid.decode() if isinstance(uid, bytes) else str(uid)
        out[key] = {**LOGGED_OUT, **json.loads(raw)}
    return out
