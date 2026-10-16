"""What the agent page does: start a browser phone, go Ready or Not Ready, hold, wrap up.

Each action asks the brain with a command (rule 2): the web never changes an
agent's or a call's state itself. The brain's answer comes back as events on the
page's live WebSocket; ``live_state`` reads the state the brain last saved.
"""

import uuid
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.agentdesk.selectors import screen_data
from apps.agents.live import LOGGED_OUT, live_state, live_states
from apps.agents.models import Agent, AgentSession, NotReadyReason, WrapupCode
from apps.calls import asterisk, bus
from apps.core.exceptions import ApplicationError, ValidationError
from apps.core.hooks import resolve_permissions
from apps.queues.models import Queue
from apps.telephony import numbers
from apps.telephony import services as telephony
from contracts import messages as m

NOT_AN_AGENT = 10204
ON_A_CALL = 10205
NOT_LISTED = 10206
PHONE_IN_USE = 10211
NO_ROUTE = 10213
PHONE_OFF = 10212


def agent_of(user) -> Agent:
    agent = Agent.objects.filter(user=user, active=True).first() if user and user.is_authenticated else None
    if agent is None:
        raise ApplicationError("You are not set up as an agent.", code=NOT_AN_AGENT)
    return agent


# The page sets this itself when the browser phone drops; it is not one of the admin's reasons.
PHONE_PROBLEM_REASON = "Phone not connected"


def desk(user) -> dict[str, Any]:
    agent = agent_of(user)
    state = live_state(agent)
    return {
        **screen_data(agent, state),
        "desk_phone": agent.phone.sip_id if agent.phone_id and agent.phone else "",
        "uid": str(agent.uid),
        "number": agent.number,
        "name": agent.name,
        "state": state,
        "not_ready_reasons": list(NotReadyReason.objects.filter(active=True).values_list("name", flat=True)),
        "wrapup_codes": list(WrapupCode.objects.filter(active=True).values_list("name", flat=True)),
        "transfer_targets": transfer_targets(agent),
    }


def browser_sip_id(agent: Agent) -> str:
    return f"web{agent.number}"


def _desk_phone(agent: Agent) -> dict[str, Any]:
    """Sign in on the desk phone an admin gave this agent: nothing is made or changed on the phone."""
    phone = agent.phone
    assert phone is not None
    if not phone.active or phone.deleted:
        raise ApplicationError(
            f"Your desk phone {phone.sip_id} is switched off in the admin. Ask your manager.", code=PHONE_OFF
        )
    endpoint = f"PJSIP/{phone.sip_id}"
    other = (
        AgentSession.objects.filter(endpoint=endpoint, logout_at=None)
        .exclude(agent=agent)
        .select_related("agent")
        .first()
    )
    if other is not None:
        raise ApplicationError(
            f"Your desk phone {phone.sip_id} is in use by agent {other.agent}, who is signed in. "
            "Ask them to sign out, or your manager to give you another phone.",
            code=PHONE_IN_USE,
        )
    AgentSession.objects.filter(agent=agent, logout_at=None).update(logout_at=timezone.now())
    AgentSession.objects.create(agent=agent, sip_account=phone, endpoint=endpoint)
    command_id = bus.send_command_on_commit(m.AGENT_LOGIN, {"agent": str(agent.uid), "endpoint": endpoint})
    return {
        "desk_phone": True,
        "sip_id": phone.sip_id,
        "password": None,  # stays in the phone
        "ws_url": None,
        "domain": None,
        "endpoint": endpoint,
        "command_id": command_id,
        "phone_state": asterisk.endpoint_state(phone.sip_id),
    }


def phone_state(user) -> dict[str, Any]:
    """The desk phone's state, which the agent page re-checks while signed in.

    Asterisk only learns a database phone's state at its next check of the phone (every 30 s), so a
    phone that just registered can read "offline" for a while: the page keeps asking."""
    agent = agent_of(user)
    phone = agent.phone if agent.phone_id else None
    return {"state": asterisk.endpoint_state(phone.sip_id) if phone else ""}


@transaction.atomic
def start_session(user) -> dict[str, Any]:
    """Where this agent takes calls for this session, and the brain told.

    A desk phone set on the agent in admin: that phone. Otherwise the agent's own browser phone
    account, with a fresh password for this session only: the previous password stops working at
    once, so an old tab or a copied password cannot register any more.
    """
    agent = agent_of(user)
    if agent.phone_id:
        return _desk_phone(agent)
    sip_id = browser_sip_id(agent)
    account, password = telephony.issue_browser_phone(actor=user, sip_id=sip_id, display_name=agent.name)

    endpoint = f"PJSIP/{sip_id}"
    AgentSession.objects.filter(agent=agent, logout_at=None).update(logout_at=timezone.now())
    AgentSession.objects.create(agent=agent, sip_account=account, endpoint=endpoint)
    command_id = bus.send_command_on_commit(m.AGENT_LOGIN, {"agent": str(agent.uid), "endpoint": endpoint})
    return {
        "desk_phone": False,
        "phone_state": "",  # the browser phone reports its own registration
        "sip_id": sip_id,
        "password": password,
        "ws_url": settings.PJSIP_WS_URL,
        "domain": settings.PJSIP_DOMAIN,
        "endpoint": endpoint,
        "command_id": command_id,
    }


@transaction.atomic
def end_session(user) -> dict[str, Any]:
    """Log out and switch the browser account off, so its password is useless until the next session.
    A desk phone is left as it is: it is a real phone."""
    agent = agent_of(user)
    if live_state(agent).get("call"):
        raise ApplicationError("Hang up before signing out.", code=ON_A_CALL)
    command_id = bus.send_command_on_commit(m.AGENT_LOGOUT, {"agent": str(agent.uid)})
    AgentSession.objects.filter(agent=agent, logout_at=None).update(logout_at=timezone.now())
    if not agent.phone_id:
        telephony.retire_browser_phone(actor=user, sip_id=browser_sip_id(agent))
    return {"command_id": command_id}


def set_availability(user, *, ready: bool, reason: str = "") -> dict[str, Any]:
    agent = agent_of(user)
    if ready:
        return {"command_id": bus.send_command(m.AGENT_READY, {"agent": str(agent.uid)})}
    reason = (reason or "").strip()
    reasons = list(NotReadyReason.objects.filter(active=True).values_list("name", flat=True))
    if not reason or (reasons and reason not in reasons and reason != PHONE_PROBLEM_REASON):
        raise ValidationError("Pick one of the listed reasons.", code=NOT_LISTED)
    return {"command_id": bus.send_command(m.AGENT_NOT_READY, {"agent": str(agent.uid), "reason": reason})}


def hold(user, *, on: bool) -> dict[str, Any]:
    agent = agent_of(user)
    return {"command_id": bus.send_command(m.CALL_HOLD if on else m.CALL_RESUME, {"agent": str(agent.uid)})}


def finish_wrapup(
    user, *, code: str, notes: str | None = None, ready: bool | None = None, reason: str = ""
) -> dict[str, Any]:
    agent = agent_of(user)
    code = (code or "").strip()
    codes = list(WrapupCode.objects.filter(active=True).values_list("name", flat=True))
    if codes and code not in codes:
        raise ValidationError("Pick one of the listed wrap-up codes.", code=NOT_LISTED)
    data: dict[str, Any] = {"agent": str(agent.uid), "code": code}
    if notes is not None:
        if len(notes) > 8000:
            raise ValidationError("Keep notes under 8,000 characters.", code=NOT_LISTED)
        data["notes"] = notes.strip()
    if ready is not None:
        data["ready"] = ready
        if not ready:
            reason = (reason or "").strip()
            reasons = list(NotReadyReason.objects.filter(active=True).values_list("name", flat=True))
            if not reason or (reasons and reason not in reasons):
                raise ValidationError("Pick one of the listed reasons.", code=NOT_LISTED)
            data["reason"] = reason
    return {"command_id": bus.send_command(m.AGENT_WRAPUP_DONE, data)}


def change_wrapup_code(user, *, call: str, code: str) -> dict[str, Any]:
    """Correct the last call's wrap-up code (the brain allows it for 2 minutes, UX_PRINCIPLES.md 4)."""
    agent = agent_of(user)
    code = (code or "").strip()
    if code not in set(WrapupCode.objects.filter(active=True).values_list("name", flat=True)):
        raise ValidationError("Pick one of the listed wrap-up codes.", code=NOT_LISTED)
    data = {"agent": str(agent.uid), "call": str(call), "code": code}
    return {"command_id": bus.send_command(m.AGENT_WRAPUP_CODE_CHANGE, data)}


# The fields each page signal may carry (UX_PRINCIPLES.md section 6); anything else is dropped.
UX_FIELDS = {
    m.UX_OFFER_SHOWN: ("shown_at",),
    m.UX_AI_SUMMARY_SHOWN: ("where",),
    m.UX_AI_SUMMARY_WRONG: ("note",),
    m.UX_AI_QUEUE_WRONG: ("queue",),
    m.UX_ACTION: ("action", "ms", "ok"),
}


def record_ux_signal(
    user, *, signal: str, signal_id: str, call: str | None, data: dict | None
) -> dict[str, Any]:
    """A measurement from the agent page, stamped with this agent; stored once per ``signal_id``."""
    agent = agent_of(user)
    if signal not in UX_FIELDS:
        raise ValidationError("Not a page signal.", code=NOT_LISTED)
    try:
        event_id = str(uuid.UUID(signal_id))
    except (TypeError, ValueError) as exc:
        raise ValidationError("signalId must be a UUID.", code=NOT_LISTED) from exc
    given = data if isinstance(data, dict) else {}
    clean: dict[str, Any] = {"agent": str(agent.uid), "call": str(call or "")[:64]}
    for key in UX_FIELDS[signal]:
        value = given.get(key, "" if key != "ok" else True)
        clean[key] = (
            value[:500] if isinstance(value, str) else value if isinstance(value, bool | int | float) else ""
        )
    bus.publish_ux(signal, clean, event_id)
    return {"command_id": event_id}


# ── transfers and conference (M5) ─────────────────────────────────────────
TRANSFER_KINDS = ("blind", "consult")
TARGET_KINDS = ("agent", "queue", "number")


def transfer_targets(agent: Agent) -> dict[str, Any]:
    """Whom the agent can hand a call to: the other agents (with their live state) and the queues."""
    states = live_states()
    others = []
    for other in Agent.objects.filter(active=True).exclude(pk=agent.pk).order_by("number"):
        state = states.get(str(other.uid), LOGGED_OUT)
        others.append(
            {
                "uid": str(other.uid),
                "number": other.number,
                "name": other.name,
                "availability": state["availability"],
                "activity": state["activity"],
            }
        )
    return {
        "agents": others,
        "queues": list(Queue.objects.filter(active=True).values_list("name", flat=True)),
    }


def _may_dial(user, target: str) -> None:
    """M4b: an outside number needs a route that allows it, and that route's permission."""
    number = numbers.outside_number(target)
    if number is None:
        return  # an internal number: a SIP id or an extension
    route = numbers.route_for(number)
    if route is None:
        raise ApplicationError(f"{number} cannot be called: no outbound route allows it.", code=NO_ROUTE)
    if not (user.is_superuser or route.required_permission in resolve_permissions(user)):
        raise ApplicationError("You are not allowed to call outside numbers.", code=NO_ROUTE)


def transfer(
    user, *, kind: str, target_kind: str, target: str, skills: dict[str, int] | None = None
) -> dict[str, Any]:
    agent = agent_of(user)
    if kind not in TRANSFER_KINDS or target_kind not in TARGET_KINDS or not (target or "").strip():
        raise ValidationError(
            "A transfer is blind or consult, to an agent, a queue or a number.", code=NOT_LISTED
        )
    if target_kind == "number":
        _may_dial(user, target.strip())
    data: dict[str, Any] = {
        "agent": str(agent.uid),
        "kind": kind,
        "target_kind": target_kind,
        "target": target.strip(),
    }
    if skills:
        data["skills"] = {str(name): int(level) for name, level in skills.items()}
    return {"command_id": bus.send_command(m.CALL_TRANSFER, data)}


def transfer_step(user, step: str, who: str | None = None) -> dict[str, Any]:
    """Complete or cancel a consult, start a conference, or drop someone from it (who: other | me)."""
    agent = agent_of(user)
    data: dict[str, Any] = {"agent": str(agent.uid)}
    if step == m.CONFERENCE_DROP:
        if who not in ("other", "me"):
            raise ValidationError("Drop the other party or yourself.", code=NOT_LISTED)
        data["who"] = who
    return {"command_id": bus.send_command(step, data)}
