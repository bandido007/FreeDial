"""A call as the core keeps it, its states and the names it gives things in Asterisk.

Shared by the brain's decisions (``brain``) and its transfers (``transfers``).
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from brain.core.agents import Activity, Agent
from brain.core.config import Requirement
from contracts import messages as m

CONSULT_PREFIX = "algodial-consult-"  # the private bridge of an agent and a consulted party


@dataclass
class Transfer:
    """A transfer under way (M5). One per call at a time."""

    id: str
    kind: str  # blind | consult
    target_kind: str  # agent | queue | number
    target: str  # agent uid, queue name or number
    by: str  # the agent who started it
    leg: str | None = None  # the target's channel
    target_agent: str | None = None
    bridge: str | None = None  # consult: the agent and the target talk here, the customer on hold
    state: str = "ringing"  # ringing | consulting


BRIDGE_PREFIX = "algodial-call-"
AGENT_LEG_PREFIX = "agent-"
AI_LEG_PREFIX = "ai-"  # the AI agent's media leg (ARI externalMedia over WebSocket)
AI_MEDIA_FORMAT = "slin24"  # 24 kHz 16-bit: what OpenAI's speech models take and give

# Q.850 causes on an agent leg that never answered, and what they mean for the offer.
OFFER_FAILURES = {
    3: "offline",  # no route to destination
    17: "rejected",  # user busy (SIP 486)
    18: "no_answer",  # no user responding
    19: "no_answer",  # no answer: the ring timeout ran out
    20: "offline",  # subscriber absent
    21: "rejected",  # call rejected (SIP 603)
    27: "offline",  # destination out of order
}
# An agent who misses an offer this way is offered nothing until Ready again.
NOT_RESPONDING = ("no_answer", "offline")
BUSY = (Activity.OFFERED, Activity.TALKING, Activity.ON_HOLD)
WRAPUP_AFTER_RESTART = 60  # seconds left for a wrap-up a restarted brain finds (its timer died)


class CallState(StrEnum):
    AI = "ai"  # an AI agent talks with the caller (M8); no queue yet
    WAITING = "waiting"  # in the bridge with music, no agent ringing
    OFFERING = "offering"  # an agent's phone is ringing
    CONNECTED = "connected"
    VOICEMAIL = "voicemail"  # out of the queue: the caller hears the prompt, then is recorded
    ENDED = "ended"


OPEN = (CallState.WAITING, CallState.OFFERING)  # still looking for an agent
TRANSFER_COMMANDS = (
    m.CALL_TRANSFER,
    m.TRANSFER_COMPLETE,
    m.TRANSFER_CANCEL,
    m.CONFERENCE_START,
    m.CONFERENCE_DROP,
)


@dataclass
class Call:
    id: str  # the linkedid every leg shares
    caller: str  # the customer's channel id
    entry: str
    queue: str  # queue uid
    bridge: str
    caller_id: str  # what the agent's phone shows
    queued_at: float
    state: CallState = CallState.WAITING
    offers: dict[str, str] = field(default_factory=dict)  # ringing agent leg -> agent uid
    agent_leg: str | None = None
    agent: str | None = None
    tried: set[str] = field(default_factory=set)  # agents who missed or refused it this round
    retry_pending: bool = False
    held: bool = False  # the agent put the customer on hold: agent out of the bridge, music on
    caller_number: str = ""
    caller_name: str = ""
    queue_entered_at: float = 0.0  # skill rules count from here (reset by an overflow)
    visited: set[str] = field(default_factory=set)  # queues it overflowed out of
    widen_at: float | None = None  # waiting for a wider skill rule from this many seconds
    # The routing ticket (ROUTING_DESIGN.md): language, priority and extra skills, and who set them last.
    language: str | None = None
    priority: int | None = None  # None: the queue's priority
    extra: tuple[Requirement, ...] = ()
    ticket_source: str = "entry_point"
    callback_number: str = ""  # the caller-ID number to ring back (not the endpoint: a trunk has no number)
    transfer: Transfer | None = None  # a transfer under way (M5)
    third: tuple[str, str | None] | None = None  # conference: the third party's leg and agent uid (or None)
    recording: str | None = None  # voicemail: the stored recording, once recording starts
    recorded_seconds: int | None = None
    connected_at: float | None = None  # when an agent (or the person transferred to) first answered
    # M8: the AI agent's part. ``summary`` and ``ai_reason`` travel with the offer to the agent.
    ai_agent: str | None = None
    ai_leg: str | None = None
    ai_connected: bool = False
    summary: str = ""
    ai_reason: str = ""

    def __post_init__(self) -> None:
        self.queue_entered_at = self.queue_entered_at or self.queued_at


def stasis_args(channel: dict[str, Any], app: str) -> list[str] | None:
    """The Stasis args of a channel sitting in our app, else None."""
    dialplan = channel.get("dialplan") or {}
    if dialplan.get("app_name") != "Stasis":
        return None
    name, *args = str(dialplan.get("app_data") or "").split(",")
    return args if name == app else None


def parse_time(stamp: str | None) -> float | None:
    """ARI's timestamps, e.g. 2026-10-14T04:10:59.569+0300, as epoch seconds."""
    try:
        return datetime.strptime(stamp or "", "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()
    except ValueError:
        return None


def caller_id(channel: dict[str, Any]) -> str:
    caller = channel.get("caller") or {}
    name = str(caller.get("name") or "").replace('"', "")
    number = str(caller.get("number") or "")
    return f'"{name}" <{number}>' if name else number


def max_wait_timer(call: Call) -> str:
    return f"maxwait:{call.id}"


def retry_timer(call: Call) -> str:
    return f"retry:{call.id}"


def wrapup_timer(agent: Agent) -> str:
    return f"wrapup:{agent.uid}"


def widen_timer(call: Call) -> str:
    return f"widen:{call.id}"


def ai_start_timer(call: Call) -> str:
    return f"aistart:{call.id}"


def voicemail_prompt(call: Call) -> str:
    return f"vm-{call.id}"


def voicemail_recording(call: Call) -> str:
    return f"callback-{call.id}"


VOICEMAIL_MEDIA = "sound:vm-intro"  # "Please leave your message after the tone…"
