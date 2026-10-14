"""What the supervisor page does: a live view of one's teams, a browser phone, and acting on calls and agents.

Every action is a command to the brain (rule 2). The web decides who may do what: the
permission for the mode, and only agents and calls of the supervisor's own teams.
"""

import json
import time
from typing import Any

from django.conf import settings
from django.db import transaction

from apps.agents.live import LOGGED_OUT, live_states
from apps.agents.models import Agent, NotReadyReason
from apps.calls import bus
from apps.core.exceptions import ApplicationError, ValidationError
from apps.core.hooks import resolve_permissions
from apps.queues.models import Queue
from apps.queues.queries import queue_stats
from apps.supervisor.permissions import (
    BARGE_CALLS,
    FORCE_AGENT_STATE,
    MONITOR_CALLS,
    SUPERVISE,
    WHISPER_CALLS,
)
from apps.supervisor.scope import Scope, scope_of
from apps.telephony import services as telephony
from contracts import messages as m

NOT_YOURS = 10700
NO_CALL = 10701
BAD_CHOICE = 10702
NOT_ALLOWED = 9000

MODE_PERMISSION = {"monitor": MONITOR_CALLS, "whisper": WHISPER_CALLS, "barge": BARGE_CALLS}
STATE_COMMAND = {"ready": m.AGENT_READY, "not_ready": m.AGENT_NOT_READY, "logged_out": m.AGENT_LOGOUT}


def phone_id(user) -> str:
    return f"websup{user.pk}"


def endpoint(user) -> str:
    return f"PJSIP/{phone_id(user)}"


def holds(user, code: str) -> bool:
    return user.is_superuser or code in resolve_permissions(user)


def live_calls() -> dict[str, dict[str, Any]]:
    saved = bus.client().hgetall(settings.BUS_CALLS_KEY)
    out = {}
    for call, raw in saved.items() if isinstance(saved, dict) else []:
        key = call.decode() if isinstance(call, bytes) else str(call)
        out[key] = json.loads(raw)
    return out


def desk(user) -> dict[str, Any]:
    scope = scope_of(user)
    states = live_states()
    agents = Agent.objects.filter(active=True).order_by("number")
    if not scope.everything:
        agents = agents.filter(uid__in=list(scope.agents))
    calls = [call for call in live_calls().values() if scope.has_call(call)]
    queue_rows = Queue.objects.filter(active=True).order_by("name")
    if not scope.everything:
        queue_rows = queue_rows.filter(name__in=list(scope.queues))
    now = time.time()
    queues = []
    for queue in queue_rows:
        waiting = [c for c in calls if c["queue"] == queue.name and c["state"] in ("waiting", "offering")]
        stats = queue_stats(queue)
        queues.append(
            {
                "name": queue.name,
                "waiting": len(waiting),
                "longest_wait": round(max((now - c["waiting_since"] for c in waiting), default=0)),
                "service_level_percent": stats["service_level_percent"],
                "target_percent": queue.service_level_percent,
                "target_seconds": queue.service_level_seconds,
                "offered": stats["offered"],
                "answered": stats["answered"],
            }
        )
    return {
        "user": str(user.uid),
        "name": user.get_full_name() or user.email,
        "can": [mode for mode, code in MODE_PERMISSION.items() if holds(user, code)]
        + (["force"] if holds(user, FORCE_AGENT_STATE) else []),
        "agents": [
            {
                "uid": str(agent.uid),
                "number": agent.number,
                "name": agent.name,
                **{
                    key: states.get(str(agent.uid), LOGGED_OUT).get(key)
                    for key in ("availability", "activity", "reason", "call", "idle_since")
                },
            }
            for agent in agents
        ],
        "calls": calls,
        "queues": queues,
        "not_ready_reasons": list(NotReadyReason.objects.filter(active=True).values_list("name", flat=True)),
    }


def start_session(user) -> dict[str, Any]:
    account, password = telephony.issue_browser_phone(
        actor=user, sip_id=phone_id(user), display_name=f"Supervisor {user.first_name}".strip()
    )
    return {
        "sip_id": account.sip_id,
        "password": password,
        "ws_url": settings.PJSIP_WS_URL,
        "domain": settings.PJSIP_DOMAIN,
        "endpoint": endpoint(user),
        "command_id": None,
    }


@transaction.atomic
def end_session(user) -> dict[str, Any]:
    command_id = bus.send_command(m.SUPERVISE_STOP, {"supervisor": str(user.uid)})
    telephony.retire_browser_phone(actor=user, sip_id=phone_id(user))
    return {"command_id": command_id}


def supervise(user, *, call: str, mode: str) -> dict[str, Any]:
    if mode not in MODE_PERMISSION:
        raise ValidationError("Choose monitor, whisper or barge.", code=BAD_CHOICE)
    if not holds(user, MODE_PERMISSION[mode]):
        raise ApplicationError(f"You may not {mode}.", code=NOT_ALLOWED)
    snapshot = live_calls().get(call)
    if snapshot is None or snapshot.get("state") != "connected" or not snapshot.get("agent"):
        raise ApplicationError("That call has ended or has no agent.", code=NO_CALL)
    if not scope_of(user).has_agent(snapshot["agent"]):
        raise ApplicationError("That call is not in your teams.", code=NOT_YOURS)
    data = {"supervisor": str(user.uid), "endpoint": endpoint(user), "call": call, "mode": mode}
    return {"command_id": bus.send_command(m.SUPERVISE_START, data)}


def stop(user) -> dict[str, Any]:
    return {"command_id": bus.send_command(m.SUPERVISE_STOP, {"supervisor": str(user.uid)})}


def force_agent_state(user, *, agent: str, state: str, reason: str = "") -> dict[str, Any]:
    if state not in STATE_COMMAND:
        raise ValidationError("Choose ready, not_ready or logged_out.", code=BAD_CHOICE)
    if not holds(user, FORCE_AGENT_STATE):
        raise ApplicationError("You may not change agents' states.", code=NOT_ALLOWED)
    if not scope_of(user).has_agent(agent) or not Agent.objects.filter(uid=agent, active=True).exists():
        raise ApplicationError("That agent is not in your teams.", code=NOT_YOURS)
    data: dict[str, Any] = {"agent": agent, "by": str(user.uid)}
    if state == "not_ready":
        data["reason"] = (reason or "").strip()
    return {"command_id": bus.send_command(STATE_COMMAND[state], data)}


def may_supervise(user) -> bool:
    return any(holds(user, code) for code in SUPERVISE)


def scope_for(user) -> Scope:
    return scope_of(user)
