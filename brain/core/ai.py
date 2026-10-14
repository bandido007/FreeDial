"""The AI agent's part of a call (M8).

An entry point with an AI agent: the brain answers, puts the caller in the call's bridge and
opens the AI's media leg (ARI externalMedia over WebSocket, Asterisk as the server). The AI
worker connects to that leg; it then enters the app with args ``ai,<call>,<ai agent>`` and joins
the caller's bridge. The worker listens and talks, and ends its part with a command:

- ``ai.handoff``: the AI's leg is hung up and the call joins its queue (hold music, max wait,
  skill rules). Its summary and reason go with the offer to the agent. A queue, language or
  skills it names pass the routing.update checks ("AI decides, rules check").
- ``ai.hangup``: the AI resolved the call; it ends with result ``ai_handled``.

If the worker never connects (``start_timeout``) or the leg dies, the call goes to the entry
point's fallback queue: a caller is never left talking to nobody.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from brain.core import actions as a
from brain.core.calls import (
    AI_LEG_PREFIX,
    AI_MEDIA_FORMAT,
    Call,
    CallState,
    ai_start_timer,
    max_wait_timer,
)
from brain.core.config import AiAgentConfig, Config, EntryConfig, QueueConfig
from contracts import messages as m

AI_COMMANDS = (m.AI_HANDOFF, m.AI_HANGUP)
ROUTING_FIELDS = ("queue", "language", "skills")


class AiMixin:
    if TYPE_CHECKING:  # what the AI part uses from the Brain it is part of
        config: Config
        calls: dict[str, Call]
        legs: dict[str, str]
        new_id: Callable[[], str]

        def _refuse(
            self, command_id: str, kind: str, reason: str, agent: str | None = None
        ) -> list[a.Action]: ...
        def _reject(self, channel: dict[str, Any], args: list[str], reason: str) -> list[a.Action]: ...
        def _hunt(self, call: Call, now: float) -> list[a.Action]: ...
        def _end(self, call: Call, result: str, reason: str, now: float) -> list[a.Action]: ...
        def _routing_update(self, data: dict[str, Any], command_id: str, now: float) -> list[a.Action]: ...
        def _queued(self, call: Call, reason: str) -> a.Emit: ...

    # ── start ─────────────────────────────────────────────────────────────
    def _ai_for(self, entry: EntryConfig) -> AiAgentConfig | None:
        return self.config.ai_agents.get(entry.ai_agent or "")

    def _start_ai(self, call: Call, ai: AiAgentConfig) -> list[a.Action]:
        call.state, call.ai_agent = CallState.AI, ai.uid
        call.ai_leg = AI_LEG_PREFIX + self.new_id()
        self.legs[call.ai_leg] = call.id
        return [
            a.Emit(
                m.AI_STARTED,
                {
                    "call": call.id,
                    "leg": call.ai_leg,
                    "ai_agent": ai.uid,
                    "language": call.language,
                    "entry": call.entry,
                    "caller": call.caller_number,
                    "caller_name": call.caller_name,
                },
            ),
            a.ExternalMedia(
                call.id, channel=call.ai_leg, app_args=f"ai,{call.id},{ai.uid}", format=AI_MEDIA_FORMAT
            ),
            a.SetTimer(ai_start_timer(call), ai.start_timeout),
        ]

    def _ai_answered(self, channel: dict[str, Any], args: list[str]) -> list[a.Action]:
        """The worker connected: the AI's leg joins the caller."""
        call = self.calls.get(args[1])
        leg = str(channel.get("id"))
        if call is None or call.state is not CallState.AI or leg != call.ai_leg or call.ai_connected:
            return self._reject(channel, args, "no AI part waiting for this leg")
        call.ai_connected = True
        return [
            a.CancelTimer(ai_start_timer(call)),
            a.AddToBridge(call.id, call.bridge, leg),
            a.Emit(m.AI_CONNECTED, {"call": call.id, "leg": leg}),
        ]

    # ── the worker ends its part ──────────────────────────────────────────
    def _ai_command(self, kind: str, data: dict[str, Any], command_id: str, now: float) -> list[a.Action]:
        call = self.calls.get(str(data.get("call") or ""))
        if call is None or call.state is not CallState.AI:
            return self._refuse(command_id, kind, "no call talking with an AI with that id")
        summary, reason = str(data.get("summary") or ""), str(data.get("reason") or "")
        transcript = data.get("transcript") or []
        if kind == m.AI_HANGUP:
            out = self._ai_leaves(call, "resolved", reason, summary, transcript)
            return out + self._end(call, "ai_handled", reason or "resolved by the AI", now)
        call.summary, call.ai_reason = summary, reason
        out = self._ai_leaves(call, "handoff", reason, summary, transcript)
        routing = {key: data[key] for key in ROUTING_FIELDS if data.get(key)}
        if not routing:
            return out + self._to_queue(call, self._queue_of(call), now, "ai_handoff")
        out += self._to_queue(call, self._queue_of(call), now, None, hunt=False)
        routed = self._routing_update({"call": call.id, "source": "ai", **routing}, command_id, now)
        moved = [x for x in routed if isinstance(x, a.Emit) and x.type == m.CALL_QUEUED]
        for queued in moved:
            queued.data["reason"] = "ai_handoff"  # it is the AI's handoff, to the queue the AI chose
        if not moved:  # the call waits in the entry's queue after all
            out.append(self._queued(call, "ai_handoff"))
        return (
            out + routed + self._hunt(call, now)
        )  # a rejected ticket with no fallback still looks for agents

    # ── failures ──────────────────────────────────────────────────────────
    def _ai_failed(self, call: Call, reason: str, now: float) -> list[a.Action]:
        """The AI never came up or its leg died: people take the call, in the fallback queue."""
        out = self._ai_leaves(call, "failed", reason, "", [])
        entry = self.config.entries.get(call.entry)
        fallback = self.config.queues.get((entry.fallback_queue if entry else None) or "")
        return out + self._to_queue(call, fallback or self._queue_of(call), now, "ai_failed")

    def _ai_timed_out(self, call: Call, now: float) -> list[a.Action]:
        if call.state is not CallState.AI or call.ai_connected:
            return []
        return self._ai_failed(call, "the AI did not come up", now)

    def _ai_leg_gone(self, call: Call, now: float) -> list[a.Action]:
        """The AI's leg ended without a command: the worker crashed or dropped the WebSocket."""
        leg, call.ai_leg = call.ai_leg, None
        self.legs.pop(leg or "", None)
        out: list[a.Action] = [a.Emit(m.LEG_ENDED, {"call": call.id, "leg": leg, "role": "ai"})]
        if call.state is not CallState.AI:
            return out
        return out + self._ai_failed(call, "the AI's leg ended", now)

    def _ai_caller_left(self, call: Call, now: float) -> list[a.Action]:
        out = self._ai_leaves(call, "caller_left", "the caller hung up", "", [])
        return out + self._end(call, "ai_handled", "customer hung up with the AI", now)

    # ── helpers ───────────────────────────────────────────────────────────
    def _ai_leaves(
        self, call: Call, outcome: str, reason: str, summary: str, transcript: list[Any]
    ) -> list[a.Action]:
        """The AI's part is over: its leg hung up (if still there) and ai.ended said once."""
        out: list[a.Action] = [a.CancelTimer(ai_start_timer(call))]
        if call.ai_leg:
            leg, call.ai_leg = call.ai_leg, None
            self.legs.pop(leg, None)
            out += [a.Hangup(call.id, leg), a.Emit(m.LEG_ENDED, {"call": call.id, "leg": leg, "role": "ai"})]
        call.state = CallState.WAITING  # until the caller is queued or the call ends
        out.append(
            a.Emit(
                m.AI_ENDED,
                {
                    "call": call.id,
                    "ai_agent": call.ai_agent,
                    "outcome": outcome,
                    "reason": reason,
                    "summary": summary,
                    "transcript": transcript,
                },
            )
        )
        return out

    def _queue_of(self, call: Call) -> QueueConfig | None:
        return self.config.queues.get(call.queue)

    def _to_queue(
        self, call: Call, queue: QueueConfig | None, now: float, reason: str | None, hunt: bool = True
    ) -> list[a.Action]:
        """The caller now waits for a person in ``queue``: hold music, max wait, skill rules from now.
        ``reason`` for call.queued; None: a routing decision follows and says where it waits."""
        if queue is None:
            return self._end(call, "failed", "no queue to hand the call to", now)
        call.state, call.queue, call.queue_entered_at = CallState.WAITING, queue.uid, now
        out: list[a.Action] = [
            a.StartMoh(call.id, call.bridge, queue.moh_class),
            a.SetTimer(max_wait_timer(call), queue.max_wait),
        ]
        if reason:
            out.append(self._queued(call, reason))
        return out + (self._hunt(call, now) if hunt else [])
