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
# Optional notes are archived with agent.wrapped_up. Optional ready/reason choose the next
# availability atomically, before the brain offers another waiting call. Older clients omit them.
# The AI agent (M8) or a transfer changes a waiting call's routing ticket. Only configured queues and
# skills are accepted; anything else sends the call to its entry point's fallback queue.
ROUTING_UPDATE = "routing.update"  # call, source; optional queue (name), language, skills {name: min level}
# M5: the agent on a call transfers it. kind: blind | consult; target_kind: agent (uid) | queue (name;
# blind only, optional skills) | number | ai (M8). A consult is then completed, cancelled or made a
# conference; in a conference the agent drops the other party (who=other) or leaves (who=me).
CALL_TRANSFER = "call.transfer"
TRANSFER_COMPLETE = "transfer.complete"
TRANSFER_CANCEL = "transfer.cancel"
CONFERENCE_START = "conference.start"
CONFERENCE_DROP = "conference.drop"
# M6: a supervisor listens to a call. mode: monitor (hears both sides, nobody hears them),
# whisper (only the agent hears them), barge (everyone hears everyone). endpoint: the supervisor's
# phone. Agent commands may carry "by" (the supervisor who forced the state).
SUPERVISE_START = "supervise.start"
SUPERVISE_MODE = "supervise.mode"
SUPERVISE_STOP = "supervise.stop"
# M8: the AI worker, talking to a caller, ends its part. Handoff: the call goes to a queue, the
# summary shows on the agent's offer; optional queue (name), language and skills follow the
# routing.update rules ("AI decides, rules check"). Hangup: the AI resolved the call.
# reason for a handoff: asked_for_person, ai_decided, failed_turns, sensitive_topic.
# transcript (optional): [{"who": "caller" | "ai", "text": ..., "at": seconds into the AI part}].
AI_HANDOFF = "ai.handoff"
AI_HANGUP = "ai.hangup"

COMMANDS: dict[str, tuple[str, ...]] = {
    AGENT_LOGIN: ("agent", "endpoint"),
    AGENT_LOGOUT: ("agent",),
    AGENT_READY: ("agent",),
    AGENT_NOT_READY: ("agent", "reason"),
    CONFIG_CHANGED: (),
    CALL_HOLD: ("agent",),
    CALL_RESUME: ("agent",),
    AGENT_WRAPUP_DONE: ("agent", "code"),
    ROUTING_UPDATE: ("call", "source"),
    CALL_TRANSFER: ("agent", "kind", "target_kind", "target"),
    TRANSFER_COMPLETE: ("agent",),
    TRANSFER_CANCEL: ("agent",),
    CONFERENCE_START: ("agent",),
    CONFERENCE_DROP: ("agent", "who"),
    SUPERVISE_START: ("supervisor", "endpoint", "call", "mode"),
    SUPERVISE_MODE: ("supervisor", "mode"),
    SUPERVISE_STOP: ("supervisor",),
    AI_HANDOFF: ("call", "reason", "summary"),
    AI_HANGUP: ("call", "reason"),
}

# ── Events: what happened, in the order the brain decided it ──────────────
CALL_STARTED = "call.started"  # a customer entered an entry point
CALL_OFFERED = "call.offered"  # an agent's phone is ringing for it
CALL_OFFER_FAILED = "call.offer_failed"  # reason: rejected, no_answer, offline, failed
CALL_CONNECTED = "call.connected"  # the agent answered and is in the bridge
CALL_HELD = "call.held"
CALL_RESUMED = "call.resumed"
# M9: the call starts waiting in a queue. reason: entry, overflow, routing, ai_handoff, ai_failed,
# transfer, transfer_back, adopted. Reports measure each queue's waits from here.
CALL_QUEUED = "call.queued"
CALL_OVERFLOWED = "call.overflowed"  # max wait or a full queue sent it to another queue
CALL_VOICEMAIL = "call.voicemail"  # overflow to voicemail: the caller is asked to leave a message
CALLBACK_REQUESTED = "callback.requested"  # a callback task, with the message if one was left
ROUTING_TICKET = "routing.ticket"  # the call's queue, language, priority and skills, and who set them
ROUTING_AI_TAG_REJECTED = "routing.ai_tag_rejected"  # an AI decision naming what is not configured
TRANSFER_STARTED = "transfer.started"  # agent = who transfers; target_agent when the target is an agent
TRANSFER_ANSWERED = "transfer.answered"
TRANSFER_COMPLETED = "transfer.completed"
TRANSFER_CANCELLED = "transfer.cancelled"
TRANSFER_FAILED = "transfer.failed"  # reason: rejected, no_answer, offline, failed
CONFERENCE_STARTED = "conference.started"
CALL_PARTY_LEFT = "call.party_left"  # someone left a conference; the call goes on
SUPERVISE_STARTED = "supervise.started"
SUPERVISE_CHANGED = "supervise.changed"
SUPERVISE_STOPPED = "supervise.stopped"
AGENT_FORCED = "agent.forced"  # a supervisor changed an agent's availability
# M8: the AI agent's part of a call. started: its media leg is being opened (the worker connects to
# it); connected: the leg is up and bridged with the caller; ended: outcome handoff | resolved |
# caller_left | failed, with the AI's summary and transcript.
AI_STARTED = "ai.started"
AI_CONNECTED = "ai.connected"
AI_ENDED = "ai.ended"
CALL_ENDED = "call.ended"  # result: answered, abandoned, overflow, voicemail, ai_handled, failed
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
    CALL_QUEUED: ("call", "queue", "reason"),
    CALL_OVERFLOWED: ("call", "from_queue", "to_queue", "reason"),
    CALL_VOICEMAIL: ("call", "queue", "reason"),
    CALLBACK_REQUESTED: ("call", "queue", "caller", "recording", "duration"),
    ROUTING_TICKET: ("call", "queue", "language", "priority", "skills", "source"),
    ROUTING_AI_TAG_REJECTED: ("call", "source", "rejected"),
    TRANSFER_STARTED: ("call", "transfer", "kind", "target_kind", "target", "agent"),
    TRANSFER_ANSWERED: ("call", "transfer", "leg", "agent"),
    TRANSFER_COMPLETED: ("call", "transfer"),
    TRANSFER_CANCELLED: ("call", "transfer", "reason"),
    TRANSFER_FAILED: ("call", "transfer", "reason"),
    CONFERENCE_STARTED: ("call", "transfer", "legs"),
    CALL_PARTY_LEFT: ("call", "leg", "reason"),
    SUPERVISE_STARTED: ("call", "supervisor", "mode", "agent"),
    SUPERVISE_CHANGED: ("call", "supervisor", "mode"),
    SUPERVISE_STOPPED: ("call", "supervisor", "reason"),
    AGENT_FORCED: ("agent", "by", "state"),
    AI_STARTED: ("call", "leg", "ai_agent"),
    AI_CONNECTED: ("call", "leg"),
    AI_ENDED: ("call", "outcome", "reason"),
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
