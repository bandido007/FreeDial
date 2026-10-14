"""The command and event types of version 1, and the fields each one must carry.

Commands go web -> brain on ``algodial:commands``; events go brain -> writer (and
the live pages) on ``algodial:events``. Within version 1 changes are additive
only: a new type, or a new optional field. A reader skips a type it does not
know and fields it does not use.

Ids are always strings: ``call`` is the call's linkedid, ``leg`` a channel id
(Asterisk's uniqueid), ``agent`` the agent's public uid.
"""

from contracts.envelope import Command, ContractError, Event

# ── Commands: the web asks, the brain decides ─────────────────────────────
AGENT_LOGIN = "agent.login"  # this agent takes calls on this endpoint, e.g. PJSIP/1001
AGENT_LOGOUT = "agent.logout"
AGENT_READY = "agent.ready"
AGENT_NOT_READY = "agent.not_ready"
CONFIG_CHANGED = "config.changed"  # reload agents, queues and entry points
CALL_HOLD = "call.hold"  # the agent puts the customer on hold (music); the agent leaves the bridge
CALL_RESUME = "call.resume"
AGENT_WRAPUP_DONE = "agent.wrapup_done"  # the agent finished the after-call work, with a wrap-up code

COMMANDS: dict[str, tuple[str, ...]] = {
    AGENT_LOGIN: ("agent", "endpoint"),
    AGENT_LOGOUT: ("agent",),
    AGENT_READY: ("agent",),
    AGENT_NOT_READY: ("agent", "reason"),
    CONFIG_CHANGED: (),
    CALL_HOLD: ("agent",),
    CALL_RESUME: ("agent",),
    AGENT_WRAPUP_DONE: ("agent", "code"),
}

# ── Events: what happened, in the order the brain decided it ──────────────
CALL_STARTED = "call.started"  # a customer entered an entry point
CALL_OFFERED = "call.offered"  # an agent's phone is ringing for it
CALL_OFFER_FAILED = "call.offer_failed"  # reason: rejected, no_answer, offline, failed
CALL_CONNECTED = "call.connected"  # the agent answered and is in the bridge
CALL_HELD = "call.held"
CALL_RESUMED = "call.resumed"
CALL_OVERFLOWED = "call.overflowed"  # max wait or a full queue sent it to another queue
CALL_ENDED = "call.ended"  # result: answered, abandoned, overflow, failed
LEG_ENDED = "leg.ended"
STASIS_REJECTED = "stasis.rejected"  # a channel with unknown args, hung up (rule 3)
AGENT_STATE = "agent.state"  # the agent's whole state after a change
AGENT_WRAPPED_UP = "agent.wrapped_up"  # by: agent (with a code) or timeout
COMMAND_REJECTED = "command.rejected"  # a command the brain could not carry out, and why
BRAIN_RECONCILED = "brain.reconciled"  # after (re)connecting to ARI: what it adopted and cleaned

EVENTS: dict[str, tuple[str, ...]] = {
    CALL_STARTED: ("call", "leg", "entry", "queue", "caller", "sip_call_id"),
    CALL_OFFERED: ("call", "leg", "agent", "endpoint"),
    CALL_OFFER_FAILED: ("call", "leg", "agent", "reason"),
    CALL_CONNECTED: ("call", "leg", "agent"),
    CALL_HELD: ("call", "agent"),
    CALL_RESUMED: ("call", "agent"),
    CALL_OVERFLOWED: ("call", "from_queue", "to_queue", "reason"),
    CALL_ENDED: ("call", "result", "reason"),
    LEG_ENDED: ("call", "leg", "role"),
    STASIS_REJECTED: ("channel", "args", "reason"),
    AGENT_STATE: ("agent", "number", "availability", "activity", "reason", "call", "endpoint"),
    AGENT_WRAPPED_UP: ("agent", "call", "code", "by"),
    COMMAND_REJECTED: ("command_id", "type", "reason"),
    BRAIN_RECONCILED: ("adopted", "hung_up", "bridges_deleted"),
}


def missing_fields(message: Command | Event) -> list[str]:
    """The required fields a message lacks. Empty for an unknown type: readers skip those."""
    table = COMMANDS if isinstance(message, Command) else EVENTS
    return [name for name in table.get(message.type, ()) if name not in message.data]


def check(message: Command | Event) -> None:
    """Raise :class:`ContractError` when a known type lacks a required field."""
    missing = missing_fields(message)
    if missing:
        raise ContractError(f"{message.type} lacks {', '.join(missing)}")
