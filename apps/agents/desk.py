"""What the agent page does: start a browser phone, go Ready or Not Ready, hold, wrap up.

Each action asks the brain with a command (rule 2): the web never changes an
agent's or a call's state itself. The brain's answer comes back as events on the
page's live WebSocket; ``live_state`` reads the state the brain last saved.
"""

import json
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.agents.models import Agent, AgentSession, NotReadyReason, WrapupCode
from apps.calls import bus
from apps.core.exceptions import ApplicationError, ValidationError
from apps.telephony import services as telephony
from apps.telephony.enums import SipAccountKindChoices
from apps.telephony.models import SipAccount
from contracts import messages as m

NOT_AN_AGENT = 10204
ON_A_CALL = 10205
NOT_LISTED = 10206

LOGGED_OUT = {
    "availability": "logged_out",
    "activity": "idle",
    "reason": "",
    "call": None,
    "wrapup_call": None,
    "endpoint": None,
}


def agent_of(user) -> Agent:
    agent = Agent.objects.filter(user=user, active=True).first() if user and user.is_authenticated else None
    if agent is None:
        raise ApplicationError("You are not set up as an agent.", code=NOT_AN_AGENT)
    return agent


def live_state(agent: Agent) -> dict[str, Any]:
    """The agent's state as the brain last saved it (Redis), or logged out."""
    raw = bus.client().hget(settings.BUS_AGENTS_KEY, str(agent.uid))
    return {**LOGGED_OUT, **json.loads(raw)} if isinstance(raw, (str, bytes)) else dict(LOGGED_OUT)


def desk(user) -> dict[str, Any]:
    agent = agent_of(user)
    return {
        "uid": str(agent.uid),
        "number": agent.number,
        "name": agent.name,
        "state": live_state(agent),
        "not_ready_reasons": list(NotReadyReason.objects.filter(active=True).values_list("name", flat=True)),
        "wrapup_codes": list(WrapupCode.objects.filter(active=True).values_list("name", flat=True)),
    }


def browser_sip_id(agent: Agent) -> str:
    return f"web{agent.number}"


@transaction.atomic
def start_session(user) -> dict[str, Any]:
    """The agent's own browser phone account, with a fresh password for this session only.

    The previous password stops working at once, so an old tab or a copied password
    cannot register any more. The brain is told where to ring the agent.
    """
    agent = agent_of(user)
    sip_id = browser_sip_id(agent)
    password = telephony.generate_password()
    account = SipAccount.objects.filter(sip_id=sip_id).first()
    if account is None:
        account = telephony.create_sip_account(
            actor=user,
            sip_id=sip_id,
            kind=SipAccountKindChoices.BROWSER.value,
            password=password,
            display_name=agent.name,
        )
    else:
        account = telephony.update_sip_account(uid=account.uid, actor=user, password=password)
        if not account.active:
            telephony.set_sip_account_active(uid=account.uid, actor=user, active=True)

    endpoint = f"PJSIP/{sip_id}"
    AgentSession.objects.filter(agent=agent, logout_at=None).update(logout_at=timezone.now())
    AgentSession.objects.create(agent=agent, sip_account=account, endpoint=endpoint)
    command_id = bus.send_command_on_commit(m.AGENT_LOGIN, {"agent": str(agent.uid), "endpoint": endpoint})
    return {
        "sip_id": sip_id,
        "password": password,
        "ws_url": settings.PJSIP_WS_URL,
        "domain": settings.PJSIP_DOMAIN,
        "endpoint": endpoint,
        "command_id": command_id,
    }


@transaction.atomic
def end_session(user) -> dict[str, Any]:
    """Log out and switch the browser account off, so its password is useless until the next session."""
    agent = agent_of(user)
    if live_state(agent).get("call"):
        raise ApplicationError("Hang up before signing out.", code=ON_A_CALL)
    command_id = bus.send_command_on_commit(m.AGENT_LOGOUT, {"agent": str(agent.uid)})
    AgentSession.objects.filter(agent=agent, logout_at=None).update(logout_at=timezone.now())
    account = SipAccount.objects.filter(sip_id=browser_sip_id(agent), active=True).first()
    if account is not None:
        telephony.set_sip_account_active(uid=account.uid, actor=user, active=False)
    return {"command_id": command_id}


def set_availability(user, *, ready: bool, reason: str = "") -> dict[str, Any]:
    agent = agent_of(user)
    if ready:
        return {"command_id": bus.send_command(m.AGENT_READY, {"agent": str(agent.uid)})}
    reason = (reason or "").strip()
    reasons = list(NotReadyReason.objects.filter(active=True).values_list("name", flat=True))
    if reasons and reason not in reasons:
        raise ValidationError("Pick one of the listed reasons.", code=NOT_LISTED)
    return {"command_id": bus.send_command(m.AGENT_NOT_READY, {"agent": str(agent.uid), "reason": reason})}


def hold(user, *, on: bool) -> dict[str, Any]:
    agent = agent_of(user)
    return {"command_id": bus.send_command(m.CALL_HOLD if on else m.CALL_RESUME, {"agent": str(agent.uid)})}


def finish_wrapup(user, *, code: str) -> dict[str, Any]:
    agent = agent_of(user)
    code = (code or "").strip()
    codes = list(WrapupCode.objects.filter(active=True).values_list("name", flat=True))
    if codes and code not in codes:
        raise ValidationError("Pick one of the listed wrap-up codes.", code=NOT_LISTED)
    return {"command_id": bus.send_command(m.AGENT_WRAPUP_DONE, {"agent": str(agent.uid), "code": code})}
