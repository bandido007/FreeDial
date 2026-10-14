"""Every call decision, as pure state machines.

The service hands the core one input at a time (an ARI event, a command, a timer,
a failed action) with the current time, and gets back the actions to perform
(:mod:`brain.core.actions`). Nothing here waits, sleeps or talks to anything, so
each decision is atomic and the same inputs always give the same actions.

A call to an entry point:
  customer enters Stasis (args: inbound, <entry>, <endpoint>, <linkedid>)
    -> answer, own mixing bridge with hold music, max-wait timer
    -> ring the agent(s) the queue's strategy picks among the free members who match the
       skill rule for how long the caller has waited (args: agent, <call>, <agent>)
  the agent's phone rejects, rings out or is offline
    -> the next agent; the one who rang out becomes Not responding
  nobody matches yet -> a timer for the second the next, wider skill rule starts
  max wait or max callers -> the queue's overflow: another queue, voicemail (then a callback
       task), or hang up
  an agent answers -> music off, agent into the bridge, any other ringing phone stops
  either side leaves -> every leg hung up and the bridge deleted (rule 8)
"""

from collections.abc import Callable, Iterable
from typing import Any

from brain.core import actions as a
from brain.core import routing, strategies
from brain.core.agents import Activity, Agent, Availability
from brain.core.ai import AI_COMMANDS, AiMixin
from brain.core.calls import (  # noqa: F401 (re-exported: tests and the service import them from here)
    AGENT_LEG_PREFIX,
    BRIDGE_PREFIX,
    BUSY,
    CONSULT_PREFIX,
    NOT_RESPONDING,
    OFFER_FAILURES,
    OPEN,
    TRANSFER_COMMANDS,
    VOICEMAIL_MEDIA,
    WRAPUP_AFTER_RESTART,
    Call,
    CallState,
    Transfer,
    ai_start_timer,
    caller_id,
    max_wait_timer,
    parse_time,
    retry_timer,
    stasis_args,
    voicemail_prompt,
    voicemail_recording,
    widen_timer,
    wrapup_timer,
)
from brain.core.config import Config, EntryConfig, OverflowAction, QueueConfig, Requirement, Strategy
from brain.core.supervision import SUPERVISOR_COMMANDS, Supervision, SupervisionMixin
from brain.core.transfers import TransferMixin
from contracts import messages as m

FORCEABLE = (m.AGENT_READY, m.AGENT_NOT_READY, m.AGENT_LOGOUT)  # with "by": a supervisor forced it


class Brain(TransferMixin, SupervisionMixin, AiMixin):
    def __init__(self, config: Config, *, app: str, new_id: Callable[[], str]):
        self.app = app
        self.new_id = new_id
        self.config = Config()
        self.agents: dict[str, Agent] = {}
        self.calls: dict[str, Call] = {}
        self.legs: dict[str, str] = {}  # channel id -> call id
        self.last_rung: dict[str, str] = {}  # queue uid -> agent uid (round robin)
        self.answered: dict[str, dict[str, int]] = {}  # queue uid -> agent uid -> calls answered
        self.supervisions: dict[str, Supervision] = {}  # supervisor uid -> what they supervise
        self.load_config(config, now=0.0)

    # ── configuration and restart ─────────────────────────────────────────
    def load_config(self, config: Config, now: float) -> list[a.Action]:
        """Take a new configuration. Agents removed from it leave once their call ends."""
        self.config = config
        out: list[a.Action] = []
        for uid, settings in config.agents.items():
            agent = self.agents.get(uid)
            if agent is None:
                self.agents[uid] = Agent(uid=uid, number=settings.number, name=settings.name)
            else:
                agent.number, agent.name, agent.removed = settings.number, settings.name, False
        for agent in list(self.agents.values()):
            if agent.uid not in config.agents:
                agent.removed = True
                if agent.call is None:
                    out += self._drop(agent)
        return out + self._offer_waiting(now)

    def restore_agents(self, saved: Iterable[dict[str, Any]]) -> None:
        """Agent states saved before a restart. Reconcile then fixes their activity."""
        for state in saved:
            agent = self.agents.get(str(state.get("agent")))
            if agent is not None:
                agent.restore(state)

    # ── inputs ────────────────────────────────────────────────────────────
    def on_ari(self, event: dict[str, Any], now: float) -> list[a.Action]:
        kind = event.get("type")
        channel = event.get("channel") or {}
        if kind == "StasisStart":
            args = [str(arg) for arg in event.get("args") or []]
            if args[:1] == ["inbound"] and len(args) >= 2:
                return self._inbound(channel, args, now)
            if args[:1] == ["agent"] and len(args) >= 3:
                return self._agent_answered(channel, args, now)
            if args[:1] == ["transfer"] and len(args) >= 3:
                return self._transfer_answered(channel, args, now)
            if args[:1] == ["supervisor"] and len(args) >= 3:
                return self._supervisor_answered(channel, args, now)
            if args[:1] == ["snoop"] and len(args) >= 3:
                return self._snoop_started(channel, args)
            if args[:1] == ["ai"] and len(args) >= 3:
                return self._ai_answered(channel, args)
            return self._reject(channel, args, "unknown args")
        if kind in ("StasisEnd", "ChannelDestroyed"):
            return self._leg_gone(str(channel.get("id")), event.get("cause"), now)
        if kind == "PlaybackFinished":
            playback = event.get("playback") or {}
            return self._prompt_done(str(playback.get("id")), now)
        if kind in ("RecordingFinished", "RecordingFailed"):
            recording = event.get("recording") or {}
            return self._message_left(str(recording.get("name")), recording.get("duration"), now)
        return []

    def on_timer(self, key: str, now: float) -> list[a.Action]:
        kind, _, call_id = key.partition(":")
        if kind == "wrapup":
            agent = self.agents.get(call_id)
            return self._wrapped_up(agent, "", "timeout", now) if agent else []
        call = self.calls.get(call_id)
        if call is None:
            return []
        if kind == "aistart":
            return self._ai_timed_out(call, now)
        if kind == "maxwait" and call.state in (CallState.WAITING, CallState.OFFERING):
            return self._overflow(call, "max wait reached", now)
        if kind == "widen":
            call.widen_at = None
            return self._hunt(call, now)
        if kind == "retry":
            call.retry_pending = False
            call.tried.clear()
            return self._hunt(call, now)
        return []

    def on_failed(self, action: a.AriAction, error: str, now: float) -> list[a.Action]:
        """An ARI action failed. A failed ring is an offline phone; anything else ends the call."""
        call = self.calls.get(action.call)
        if call is None:
            return []
        if isinstance(action, a.Originate):
            if action.channel not in call.offers:
                return []
            return self._offer_failed(call, action.channel, "offline", None, now)
        if isinstance(action, a.ExternalMedia):
            if call.state is not CallState.AI or action.channel != call.ai_leg:
                return []
            self.legs.pop(action.channel, None)
            call.ai_leg = None
            return self._ai_failed(call, f"could not open the AI's media: {error}", now)
        return self._end(call, "failed", f"{type(action).__name__} failed: {error}", now)

    def on_command(self, kind: str, data: dict[str, Any], command_id: str, now: float) -> list[a.Action]:
        """Agent requests. Each is idempotent: asking twice changes nothing the second time (rule 12)."""
        if kind == m.ROUTING_UPDATE:
            return self._routing_update(data, command_id, now)
        if kind in SUPERVISOR_COMMANDS:
            return self._supervise_command(kind, data, command_id)
        if kind in AI_COMMANDS:
            return self._ai_command(kind, data, command_id, now)
        out = self._agent_command(kind, data, command_id, now)
        if (
            data.get("by")
            and kind in FORCEABLE
            and not any(isinstance(x, a.Emit) and x.type == m.COMMAND_REJECTED for x in out)
        ):
            state = {m.AGENT_READY: "ready", m.AGENT_NOT_READY: "not_ready", m.AGENT_LOGOUT: "logged_out"}[
                kind
            ]
            forced = {
                "agent": data.get("agent"),
                "by": data["by"],
                "state": state,
                "reason": data.get("reason", ""),
            }
            out.append(a.Emit(m.AGENT_FORCED, forced))
        return out

    def _agent_command(self, kind: str, data: dict[str, Any], command_id: str, now: float) -> list[a.Action]:
        agent = self.agents.get(str(data.get("agent")))
        if agent is None or agent.removed:
            return self._refuse(command_id, kind, "unknown agent")

        if kind == m.AGENT_LOGIN:
            endpoint = str(data["endpoint"])
            if agent.availability is not Availability.LOGGED_OUT and agent.endpoint == endpoint:
                return []
            if agent.call is not None:
                return self._refuse(command_id, kind, "the agent is on a call", agent.uid)
            agent.endpoint = endpoint
            if agent.availability is Availability.LOGGED_OUT:
                agent.availability, agent.reason, agent.activity = (
                    Availability.NOT_READY,
                    "login",
                    Activity.IDLE,
                )
            return self._changed(agent)

        if kind == m.AGENT_LOGOUT:
            if agent.availability is Availability.LOGGED_OUT:
                return []
            if agent.call is not None:
                return self._refuse(command_id, kind, "the agent is on a call", agent.uid)
            out = [a.CancelTimer(wrapup_timer(agent))] if agent.activity is Activity.WRAPUP else []
            agent.availability, agent.reason, agent.activity = Availability.LOGGED_OUT, "", Activity.IDLE
            agent.endpoint = agent.wrapup_call = None
            return out + self._changed(agent)

        if agent.availability is Availability.LOGGED_OUT:
            return self._refuse(command_id, kind, "the agent is not logged in", agent.uid)

        if kind == m.AGENT_READY:
            if agent.availability is Availability.READY and agent.activity is not Activity.NOT_RESPONDING:
                return []
            agent.availability, agent.reason = Availability.READY, ""
            if agent.activity is Activity.NOT_RESPONDING:
                agent.activity = Activity.IDLE
            agent.idle_since = now
            return self._changed(agent) + self._offer_waiting(now)

        if kind == m.AGENT_NOT_READY:
            reason = str(data.get("reason") or "")
            if agent.availability is Availability.NOT_READY and agent.reason == reason:
                return []
            agent.availability, agent.reason = Availability.NOT_READY, reason
            if agent.activity is Activity.NOT_RESPONDING:
                agent.activity = Activity.IDLE
            return self._changed(agent)

        if kind == m.AGENT_WRAPUP_DONE:
            if agent.activity is not Activity.WRAPUP:
                return []  # already finished (or a repeat)
            if "ready" in data:
                agent.availability = Availability.READY if data["ready"] else Availability.NOT_READY
                agent.reason = "" if data["ready"] else str(data.get("reason") or "")
            return self._wrapped_up(agent, str(data.get("code") or ""), "agent", now, notes=data.get("notes"))

        if kind in (m.CALL_HOLD, m.CALL_RESUME):
            call = self.calls.get(agent.call or "")
            if call is None or call.state is not CallState.CONNECTED or call.agent != agent.uid:
                return self._refuse(command_id, kind, "the agent has no connected call", agent.uid)
            if call.transfer is not None or call.third is not None:
                return self._refuse(command_id, kind, "a transfer or conference is under way", agent.uid)
            return self._hold(call, agent, kind == m.CALL_HOLD)

        if kind in TRANSFER_COMMANDS:
            return self._transfer_command(kind, data, command_id, agent, now)

        return self._refuse(command_id, kind, "unknown command", agent.uid)

    def reconcile(
        self, channels: list[dict[str, Any]], bridges: list[dict[str, Any]], connected_at: float, now: float
    ) -> list[a.Action]:
        """Asterisk is the truth (rule 1). After (re)connecting, make memory match what really exists."""
        live = {str(channel["id"]): channel for channel in channels}
        out: list[a.Action] = []
        adopted: list[str] = []
        hung_up: list[str] = []
        deleted: list[str] = []

        # Calls in memory that lost a part while the brain was not listening.
        for call in list(self.calls.values()):
            if call.caller not in live:
                result = "answered" if call.agent else "abandoned"
                out += self._end(call, result, "customer left while the brain was away", now)
            elif call.bridge not in {bridge["id"] for bridge in bridges}:
                out += self._end(call, "failed", "bridge gone while the brain was away", now)
            elif call.agent_leg and call.agent_leg not in live:
                out += self._end(call, "answered", "agent left while the brain was away", now)
            elif call.transfer is not None and call.transfer.leg and call.transfer.leg not in live:
                out += self._transfer_leg_gone(call, None, now)
            elif call.ai_leg and call.ai_leg not in live:
                out += self._ai_leg_gone(call, now)
            else:
                for leg in [leg for leg in call.offers if leg not in live]:
                    out += self._offer_failed(call, leg, "failed", None, now)

        # Our bridges that no call owns: the brain restarted. Adopt what makes sense, clean the rest.
        owned = {call.bridge for call in self.calls.values()}
        in_a_bridge = {str(channel) for bridge in bridges for channel in bridge.get("channels") or []}
        on_hold = {  # agent legs in the app but out of any bridge: their customer is on hold
            leg_args[1]: channel
            for channel in live.values()
            if (leg_args := stasis_args(channel, self.app) or [])[:1] == ["agent"]
            and len(leg_args) > 2
            and str(channel["id"]) not in in_a_bridge
        }
        consults = {
            call.transfer.bridge for call in self.calls.values() if call.transfer and call.transfer.bridge
        }
        for bridge in bridges:
            name = str(bridge.get("name") or "")
            if name.startswith(CONSULT_PREFIX) and bridge["id"] not in consults:
                out += [a.DeleteBridge(str(bridge["id"]), str(bridge["id"])), a.Close(str(bridge["id"]))]
                deleted.append(str(bridge["id"]))
                continue
            if not name.startswith(BRIDGE_PREFIX) or bridge["id"] in owned:
                continue
            present = [live[str(cid)] for cid in bridge.get("channels") or [] if str(cid) in live]
            callers = [ch for ch in present if (stasis_args(ch, self.app) or [""])[0] == "inbound"]
            agents = [ch for ch in present if (stasis_args(ch, self.app) or [""])[0] == "agent"]
            ais = [ch for ch in present if (stasis_args(ch, self.app) or [""])[0] == "ai"]
            if ais and len(callers) == 1 and not agents and len(present) == 1 + len(ais):
                # The AI's conversation died with the old brain: hang up its leg, the caller waits for a person.
                call_id = str(bridge.get("name") or "")[len(BRIDGE_PREFIX) :] or str(bridge["id"])
                for ai_channel in ais:
                    ai_leg = str(ai_channel["id"])
                    out += [
                        a.Hangup(call_id, ai_leg),
                        a.Emit(m.LEG_ENDED, {"call": call_id, "leg": ai_leg, "role": "ai"}),
                    ]
                    hung_up.append(ai_leg)
                present = callers
            if len(callers) == 1 and len(agents) <= 1 and len(present) == len(callers) + len(agents):
                adoption = self._adopt(bridge, callers[0], agents[0] if agents else None, on_hold, now)
                if adoption is not None:
                    call, more = adoption
                    adopted.append(call.id)
                    out += more
                    continue
            out += self._clean_bridge(bridge, present, agents)
            hung_up += [str(channel["id"]) for channel in present]
            deleted.append(str(bridge["id"]))

        # Channels nothing drives. Ones created after connecting are skipped: their StasisStart is on its way.
        for cid, channel in live.items():
            if cid in self.legs or cid in hung_up:
                continue
            created = parse_time(channel.get("creationtime"))
            if created is not None and created >= connected_at:
                continue
            args = stasis_args(channel, self.app)
            if args is None:
                if cid.startswith(AGENT_LEG_PREFIX):  # still ringing for a call the brain lost
                    out += [a.Hangup(cid, cid), a.Close(cid)]
                    hung_up.append(cid)
                continue
            if args[:1] == ["inbound"] and cid not in in_a_bridge:
                more = self._inbound(channel, args, now)  # a customer still waiting for a decision
                if cid in self.legs:
                    adopted.append(self.legs[cid])
                else:
                    hung_up.append(cid)
                out += more
                continue
            out += [a.Hangup(cid, cid), a.Close(cid)]
            hung_up.append(cid)

        # Agents whose saved state points at a call that no longer exists.
        for agent in list(self.agents.values()):
            if agent.activity is Activity.WRAPUP:  # the timer died with the old brain
                out.append(a.SetTimer(wrapup_timer(agent), WRAPUP_AFTER_RESTART))
                continue
            if agent.call is not None and agent.call in self.calls:
                continue
            if agent.call is not None or agent.activity in BUSY:
                if agent.activity in BUSY:
                    agent.activity, agent.idle_since = Activity.IDLE, now
                agent.call = None
                out += self._changed(agent)
                if agent.removed:
                    out += self._drop(agent)

        out.append(
            a.Emit(m.BRAIN_RECONCILED, {"adopted": adopted, "hung_up": hung_up, "bridges_deleted": deleted})
        )
        return out + self._offer_waiting(now)

    # ── calls ─────────────────────────────────────────────────────────────
    def _inbound(self, channel: dict[str, Any], args: list[str], now: float) -> list[a.Action]:
        entry = args[1]
        endpoint = args[2] if len(args) > 2 else ""
        call_id = args[3] if len(args) > 3 and args[3] else str(channel["id"])
        entry_point = self.config.entries.get(entry)
        queue = self.config.queues.get(entry_point.queue) if entry_point else None
        if entry_point is None or queue is None:
            return self._reject(channel, args, f"no entry point {entry}")
        if call_id in self.calls:
            return []
        waiting = sum(1 for other in self.calls.values() if other.queue == queue.uid and other.state in OPEN)
        full = 0 < queue.max_callers <= waiting

        call = Call(
            id=call_id,
            caller=str(channel["id"]),
            entry=entry,
            queue=queue.uid,
            bridge=self.new_id(),
            caller_id=caller_id(channel),
            queued_at=now,
            language=entry_point.language,
            priority=entry_point.priority,
        )
        self.calls[call.id] = call
        self.legs[call.caller] = call.id
        caller = channel.get("caller") or {}
        call.caller_number = endpoint or str(caller.get("number") or "")
        call.caller_name = str(caller.get("name") or "")
        call.callback_number = str(caller.get("number") or "") or endpoint
        out: list[a.Action] = [
            a.Emit(
                m.CALL_STARTED,
                {
                    "call": call.id,
                    "leg": call.caller,
                    "entry": entry,
                    "queue": queue.name,
                    "caller": endpoint or str(caller.get("number") or ""),
                    "caller_name": str(caller.get("name") or ""),
                    "sip_call_id": str(channel.get("protocol_id") or ""),
                    "language": call.language,
                    "priority": self._priority(call),
                },
            ),
            a.Answer(call.id, call.caller),
            a.CreateBridge(call.id, call.bridge, BRIDGE_PREFIX + call.id),
            a.AddToBridge(call.id, call.bridge, call.caller),
        ]
        ai = self._ai_for(entry_point)
        if ai is not None:  # M8: the AI talks first; the queue comes at handoff
            return out + self._start_ai(call, ai)
        out += [
            a.StartMoh(call.id, call.bridge, queue.moh_class),
            a.SetTimer(max_wait_timer(call), queue.max_wait),
            self._queued(call, "entry"),
        ]
        if full:
            return out + self._overflow(call, "queue full", now)
        return out + self._hunt(call, now)

    def _hunt(self, call: Call, now: float) -> list[a.Action]:
        """Offer the call to whom the queue's strategy picks among the free, matching, untried members."""
        if call.state is not CallState.WAITING:
            return []
        queue = self.config.queues.get(call.queue)
        if queue is None:
            return []
        free = [uid for uid in queue.members if uid in self.agents and self.agents[uid].can_take_a_call]
        found = routing.match(queue, self._ticket(call), now - call.queue_entered_at, self._skills(), free)
        matching = [self.agents[uid] for uid in found.agents]
        fresh = [agent for agent in matching if agent.uid not in call.tried]
        if fresh:
            ticket = self._ticket(call)
            levels = self._skills()
            chosen = strategies.choose(
                queue.strategy,
                fresh,
                members=queue.members,
                last_rung=self.last_rung.get(queue.uid),
                answered=self.answered.get(queue.uid, {}),
                new_id=self.new_id,
                scores={
                    agent.uid: routing.skill_score(levels.get(agent.uid, {}), found.step, ticket)
                    for agent in fresh
                }
                if queue.strategy == Strategy.BEST_SKILLED
                else None,
            )
            return self._offer(call, chosen, queue)
        out: list[a.Action] = []
        if (
            matching and not call.retry_pending
        ):  # every matching agent refused it: ask them again after a pause
            call.retry_pending = True
            out.append(a.SetTimer(retry_timer(call), queue.retry_delay))
        wider_at = found.next_change
        if wider_at is not None and call.widen_at != wider_at:  # the next step starts at that second
            call.widen_at = wider_at
            out.append(a.SetTimer(widen_timer(call), call.queue_entered_at + wider_at - now))
        return out

    def _ticket(self, call: Call) -> routing.Ticket:
        return routing.Ticket(call.queue, call.language, call.priority, call.extra, call.ticket_source)

    def _skills(self) -> dict[str, dict[str, int]]:
        return {uid: agent.skills for uid, agent in self.config.agents.items()}

    def _priority(self, call: Call) -> int:
        if call.priority is not None:
            return call.priority
        queue = self.config.queues.get(call.queue)
        return queue.priority if queue else 0

    def _offer(self, call: Call, agents: list[Agent], queue: QueueConfig) -> list[a.Action]:
        out: list[a.Action] = []
        for agent in agents:
            leg = AGENT_LEG_PREFIX + self.new_id()
            call.state, call.offers[leg] = CallState.OFFERING, agent.uid
            self.legs[leg] = call.id
            agent.activity, agent.call = Activity.OFFERED, call.id
            self.last_rung[queue.uid] = agent.uid
            out += [
                a.Emit(
                    m.CALL_OFFERED,
                    {
                        "call": call.id,
                        "leg": leg,
                        "agent": agent.uid,
                        "endpoint": agent.endpoint,
                        "caller": call.caller_number,
                        "caller_name": call.caller_name,
                        "queue": queue.name,
                        "summary": call.summary,  # M8: what the AI learnt, for the agent
                        "ai_reason": call.ai_reason,
                    },
                ),
                a.Originate(
                    call.id,
                    channel=leg,
                    endpoint=str(agent.endpoint),
                    app_args=f"agent,{call.id},{agent.uid}",
                    caller_id=call.caller_id,
                    originator=call.caller,
                    timeout=queue.ring_timeout,
                ),
                *self._changed(agent),
            ]
        return out

    def _offer_waiting(self, now: float) -> list[a.Action]:
        """Free agents take waiting callers: higher-priority queues first, then who waited longest."""

        def order(call: Call) -> tuple[int, float]:
            return (-self._priority(call), call.queued_at)

        out: list[a.Action] = []
        for call in sorted(self.calls.values(), key=order):
            out += self._hunt(call, now)
        return out

    def _agent_answered(self, channel: dict[str, Any], args: list[str], now: float) -> list[a.Action]:
        leg = str(channel["id"])
        call = self.calls.get(args[1])
        if call is None or leg not in call.offers:
            self.legs.pop(leg, None)
            return self._reject(channel, args, "its call is gone")

        uid = call.offers.pop(leg)
        agent = self.agents.get(uid)
        out = self._cancel_offers(call, "answered elsewhere")
        call.state, call.agent_leg, call.agent = CallState.CONNECTED, leg, uid
        call.connected_at = call.connected_at or now
        counts = self.answered.setdefault(call.queue, {})
        counts[uid] = counts.get(uid, 0) + 1
        out += [
            a.Emit(
                m.CALL_CONNECTED,
                {
                    "call": call.id,
                    "leg": leg,
                    "agent": call.agent,
                    "sip_call_id": str(channel.get("protocol_id") or ""),
                },
            ),
            a.StopMoh(call.id, call.bridge),
            a.AddToBridge(call.id, call.bridge, leg),
            a.CancelTimer(max_wait_timer(call)),
            a.CancelTimer(retry_timer(call)),
        ]
        if call.widen_at is not None:
            call.widen_at = None
            out.append(a.CancelTimer(widen_timer(call)))
        if agent is not None:
            agent.activity, agent.reason = Activity.TALKING, ""
            out += self._changed(agent)
        return out

    def _cancel_offers(self, call: Call, reason: str) -> list[a.Action]:
        """Stop every phone still ringing for this call. Not the agents' fault: they stay where they were."""
        out: list[a.Action] = []
        for leg, uid in list(call.offers.items()):
            del call.offers[leg]
            self.legs.pop(leg, None)
            out += [
                a.Hangup(call.id, leg),
                a.Emit(
                    m.CALL_OFFER_FAILED,
                    {"call": call.id, "leg": leg, "agent": uid, "reason": reason, "cause": None},
                ),
                a.Emit(m.LEG_ENDED, {"call": call.id, "leg": leg, "role": "agent", "cause": None}),
            ]
            agent = self.agents.get(uid)
            if agent is not None and agent.call == call.id:
                agent.call, agent.activity = None, Activity.IDLE
                out += self._changed(agent)
        if call.state is CallState.OFFERING:
            call.state = CallState.WAITING
        return out

    def _leg_gone(self, leg: str, cause: Any, now: float) -> list[a.Action]:
        call = self.calls.get(self.legs.get(leg, ""))
        if call is None:
            self.legs.pop(leg, None)
            return []
        if leg == call.caller:
            if call.state is CallState.AI:
                return self._ai_caller_left(call, now)
            if call.state is CallState.VOICEMAIL:
                return self._end(call, "voicemail", "caller hung up during voicemail", now)
            return self._end(call, "answered" if call.agent else "abandoned", "customer hung up", now)
        if leg == call.agent_leg:
            if call.transfer is not None or call.third is not None:
                return self._agent_left(call, now)
            return self._end(call, "answered", "agent hung up", now)
        if call.transfer is not None and leg == call.transfer.leg:
            return self._transfer_leg_gone(call, cause, now)
        if leg == call.ai_leg:
            return self._ai_leg_gone(call, now)
        supervised = self._supervision_leg_gone(call, leg)
        if supervised is not None:
            return supervised
        if call.third is not None and leg == call.third[0]:
            return self._third_left(call, "hung up", now)
        if leg in call.offers:
            reason = OFFER_FAILURES.get(cause, "failed") if isinstance(cause, int) else "failed"
            return self._offer_failed(call, leg, reason, cause, now)
        self.legs.pop(leg, None)
        return []

    def _offer_failed(self, call: Call, leg: str, reason: str, cause: Any, now: float) -> list[a.Action]:
        uid = call.offers.pop(leg, None)
        agent = self.agents.get(uid or "")
        self.legs.pop(leg, None)
        if not call.offers:
            call.state = CallState.WAITING
        out: list[a.Action] = [
            a.Emit(
                m.CALL_OFFER_FAILED,
                {
                    "call": call.id,
                    "leg": leg,
                    "agent": agent.uid if agent else None,
                    "reason": reason,
                    "cause": cause,
                },
            ),
            a.Emit(m.LEG_ENDED, {"call": call.id, "leg": leg, "role": "agent", "cause": cause}),
        ]
        if agent is not None:
            call.tried.add(agent.uid)
            if agent.call == call.id:
                agent.call = None
                queue = self.config.queues.get(call.queue)
                rang_with_others = queue is not None and queue.strategy == Strategy.RING_ALL
                if reason in NOT_RESPONDING and not (reason == "no_answer" and rang_with_others):
                    agent.activity, agent.reason = Activity.NOT_RESPONDING, reason
                else:
                    agent.activity = Activity.IDLE  # refusing keeps the agent's place in line
                out += self._changed(agent)
                if agent.removed:
                    out += self._drop(agent)
        return out + self._offer_waiting(now)

    def _end(self, call: Call, result: str, reason: str, now: float) -> list[a.Action]:
        """Total cleanup (rule 8): every leg hung up and the bridge deleted, whatever already went."""
        if call.state is CallState.ENDED:
            return []
        call.state = CallState.ENDED
        out: list[a.Action] = [a.CancelTimer(max_wait_timer(call)), a.CancelTimer(retry_timer(call))]
        if call.widen_at is not None:
            out.append(a.CancelTimer(widen_timer(call)))
        if call.ai_agent:
            out.append(a.CancelTimer(ai_start_timer(call)))
        offered = list(call.offers.values())
        transfer, third = call.transfer, call.third
        legs = [(leg, "agent") for leg in call.offers] + [(call.agent_leg, "agent")]
        if third is not None:
            legs.append((third[0], "agent" if third[1] else "external"))
        if transfer is not None and transfer.leg:
            legs.append((transfer.leg, "agent" if transfer.target_agent else "external"))
        if call.ai_leg:
            legs.append((call.ai_leg, "ai"))
        legs.append((call.caller, "customer"))
        for leg, role in legs:
            if leg:
                self.legs.pop(leg, None)
                out += [
                    a.Hangup(call.id, leg),
                    a.Emit(m.LEG_ENDED, {"call": call.id, "leg": leg, "role": role}),
                ]
        call.offers.clear()
        if result == "voicemail":
            out.append(self._callback(call))
        if transfer is not None and transfer.bridge:
            out.append(a.DeleteBridge(call.id, transfer.bridge))
        call.transfer = call.third = None
        out += self._end_supervisions(call)
        out += [
            a.DeleteBridge(call.id, call.bridge),
            a.Close(call.id),
            a.Emit(m.CALL_ENDED, {"call": call.id, "result": result, "reason": reason, "agent": call.agent}),
        ]
        del self.calls[call.id]

        people = [(call.agent, True)] + [(uid, False) for uid in offered]
        if third is not None:
            people.append((third[1], True))
        if transfer is not None:
            people.append((transfer.target_agent, transfer.state == "consulting"))
        for uid, talked in people:
            out += self._release(uid, call, talked, now)
        return out + self._offer_waiting(now)

    def live_calls(self) -> dict[str, dict[str, Any]]:
        """What a supervisor's page shows of each call now (the service stores and pushes changes)."""
        out = {}
        for call in self.calls.values():
            queue = self.config.queues.get(call.queue)
            transfer = call.transfer
            out[call.id] = {
                "call": call.id,
                "entry": call.entry,
                "queue": queue.name if queue else "",
                "caller": call.callback_number or call.caller_number,
                "caller_name": call.caller_name,
                "language": call.language,
                "priority": self._priority(call),
                "state": str(call.state),
                "agent": call.agent,
                "held": call.held,
                "waiting_since": call.queued_at,
                "queue_entered_at": call.queue_entered_at,
                "connected_at": call.connected_at,
                "ringing": sorted(call.offers.values()),
                "transfer": None
                if transfer is None
                else {
                    "kind": transfer.kind,
                    "state": transfer.state,
                    "target_kind": transfer.target_kind,
                    "target": transfer.target,
                },
                "conference": call.third is not None,
                "ai_agent": call.ai_agent,
                "summary": call.summary,
                "supervisors": [
                    {"supervisor": s.supervisor, "mode": s.mode}
                    for s in self.supervisions.values()
                    if s.call == call.id and s.answered
                ],
            }
        return out

    def _release(self, uid: str | None, call: Call, talked: bool, now: float) -> list[a.Action]:
        """An agent's part in a call is over: wrap-up (if they talked and the queue wants it) or idle."""
        agent = self.agents.get(uid or "")
        if agent is None or agent.call != call.id:
            return []
        out: list[a.Action] = []
        queue = self.config.queues.get(call.queue)
        wrapup = queue.wrapup_seconds if queue else 0
        agent.call = None
        if agent.activity in BUSY:
            agent.activity = Activity.IDLE
            if talked:
                agent.idle_since = now
                if wrapup > 0 and agent.availability is not Availability.LOGGED_OUT:
                    agent.activity, agent.wrapup_call = Activity.WRAPUP, call.id
                    out.append(a.SetTimer(wrapup_timer(agent), wrapup))
        out += self._changed(agent)
        if agent.removed:
            out += self._drop(agent)
        return out

    def _overflow(self, call: Call, reason: str, now: float) -> list[a.Action]:
        """Max wait or a full queue: the queue's overflow action (another queue, voicemail, hang up).

        A queue the call already left is never entered again, so overflow chains cannot loop.
        """
        queue = self.config.queues.get(call.queue)
        if queue is not None and queue.overflow_action == OverflowAction.VOICEMAIL:
            return self._to_voicemail(call, queue, reason, now)
        target = self.config.queues.get(queue.overflow_queue or "") if queue else None
        if queue is None or target is None or target.uid in call.visited or target.uid == queue.uid:
            return self._end(call, "overflow", reason, now)
        return self._move(call, queue, target, reason, now)

    def _move(
        self, call: Call, queue: QueueConfig, target: QueueConfig, reason: str, now: float
    ) -> list[a.Action]:
        """The call leaves ``queue`` for ``target``: offers stop, waiting and skill rules start again."""
        out = self._cancel_offers(call, "overflowed")
        call.visited.add(queue.uid)
        call.queue, call.queue_entered_at = target.uid, now
        call.tried.clear()
        call.retry_pending = False
        out += [
            a.CancelTimer(retry_timer(call)),
            a.SetTimer(max_wait_timer(call), target.max_wait),
            self._queued(call, "routing" if reason == "routing" else "overflow"),
        ]
        if call.widen_at is not None:
            call.widen_at = None
            out.append(a.CancelTimer(widen_timer(call)))
        if reason != "routing":
            out.append(
                a.Emit(
                    m.CALL_OVERFLOWED,
                    {"call": call.id, "from_queue": queue.name, "to_queue": target.name, "reason": reason},
                )
            )
        if target.moh_class != queue.moh_class:
            out.append(a.StartMoh(call.id, call.bridge, target.moh_class))
        return out + self._offer_waiting(now)

    # ── voicemail and callback ────────────────────────────────────────────
    def _to_voicemail(self, call: Call, queue: QueueConfig, reason: str, now: float) -> list[a.Action]:
        """Out of the queue: the caller leaves the bridge, hears the prompt, then is recorded."""
        out = self._cancel_offers(call, "overflowed")
        call.state = CallState.VOICEMAIL
        out += [
            a.CancelTimer(max_wait_timer(call)),
            a.CancelTimer(retry_timer(call)),
            a.Emit(m.CALL_VOICEMAIL, {"call": call.id, "queue": queue.name, "reason": reason}),
            a.StopMoh(call.id, call.bridge),
            a.RemoveFromBridge(call.id, call.bridge, call.caller),
            a.Play(call.id, call.caller, VOICEMAIL_MEDIA, voicemail_prompt(call)),
        ]
        if call.widen_at is not None:
            call.widen_at = None
            out.append(a.CancelTimer(widen_timer(call)))
        return out + self._offer_waiting(now)

    def _prompt_done(self, playback: str, now: float) -> list[a.Action]:
        call = next((c for c in self.calls.values() if voicemail_prompt(c) == playback), None)
        if call is None or call.state is not CallState.VOICEMAIL or call.recording:
            return []
        call.recording = voicemail_recording(call)
        return [a.Record(call.id, call.caller, call.recording)]

    def _message_left(self, name: str, duration: Any, now: float) -> list[a.Action]:
        call = next((c for c in self.calls.values() if c.recording == name), None)
        if call is None or call.state is not CallState.VOICEMAIL:
            return []
        call.recorded_seconds = int(duration) if isinstance(duration, int | float) else None
        return self._end(call, "voicemail", "message left", now)

    def _callback(self, call: Call) -> a.Emit:
        queue = self.config.queues.get(call.queue)
        return a.Emit(
            m.CALLBACK_REQUESTED,
            {
                "call": call.id,
                "queue": queue.name if queue else "",
                "caller": call.callback_number,
                "caller_name": call.caller_name,
                "language": call.language,
                "recording": call.recording,
                "duration": call.recorded_seconds,
            },
        )

    # ── routing tickets from the AI or a transfer ─────────────────────────
    def _routing_update(self, data: dict[str, Any], command_id: str, now: float) -> list[a.Action]:
        """Change a waiting call's ticket. Only configured queues and skills pass ("AI decides, rules check");
        anything else sends the call to its entry point's fallback queue and is logged."""
        call = self.calls.get(str(data.get("call")))
        if call is None or call.state not in OPEN:
            return self._refuse(command_id, m.ROUTING_UPDATE, "no waiting call with that id")
        source = str(data.get("source") or "")
        by_name = {queue.name.lower(): queue for queue in self.config.queues.values()}
        rejected: dict[str, list[str]] = {}
        target = self.config.queues.get(call.queue)
        if data.get("queue"):
            target = by_name.get(str(data["queue"]).lower())
            if target is None:
                rejected["queue"] = [str(data["queue"])]
        language = data.get("language") or call.language
        if language and self.config.skills.get(str(language)) != "language":
            rejected["language"] = [str(language)]
        skills = {str(name): int(level) for name, level in (data.get("skills") or {}).items()}
        unknown = sorted(name for name in skills if name not in self.config.skills)
        if unknown:
            rejected["skills"] = unknown

        current = self.config.queues.get(call.queue)
        if rejected or target is None or current is None:
            entry = self.config.entries.get(call.entry) or EntryConfig("", call.queue)
            fallback = self.config.queues.get(entry.fallback_queue or "") or current
            out: list[a.Action] = [
                a.Emit(m.ROUTING_AI_TAG_REJECTED, {"call": call.id, "source": source, "rejected": rejected})
            ]
            if fallback is not None and current is not None and fallback.uid != call.queue:
                call.ticket_source = source
                out += self._move(call, current, fallback, "routing", now)
            return out

        call.language = str(language) if language else None
        call.extra = tuple(Requirement(name, level) for name, level in skills.items())
        call.ticket_source = source
        out = [
            a.Emit(
                m.ROUTING_TICKET,
                {
                    "call": call.id,
                    "queue": target.name,
                    "language": call.language,
                    "priority": self._priority(call),
                    "skills": skills,
                    "source": source,
                },
            )
        ]
        if target.uid != call.queue:
            return out + self._move(call, current, target, "routing", now)
        call.tried.clear()
        return out + self._hunt(call, now)

    def _adopt(
        self,
        bridge: dict[str, Any],
        caller: dict[str, Any],
        agent_leg: dict[str, Any] | None,
        on_hold: dict[str, dict[str, Any]],
        now: float,
    ) -> tuple[Call, list[a.Action]] | None:
        """Take over a call a previous brain left in Asterisk."""
        args = stasis_args(caller, self.app) or []
        entry = args[1] if len(args) > 1 else ""
        entry_point = self.config.entries.get(entry)
        queue_uid = entry_point.queue if entry_point else ""
        queue = self.config.queues.get(queue_uid)
        call_id = args[3] if len(args) > 3 and args[3] else str(caller["id"])
        held = agent_leg is None and call_id in on_hold
        if held:
            agent_leg = on_hold[call_id]
        if agent_leg is None and queue is None:
            return None  # waiting, but nowhere to send it: clean it

        call = Call(
            id=call_id,
            caller=str(caller["id"]),
            entry=entry,
            queue=queue_uid,
            bridge=str(bridge["id"]),
            caller_id=caller_id(caller),
            queued_at=now,
            language=entry_point.language if entry_point else None,
            priority=entry_point.priority if entry_point else None,
        )
        self.calls[call.id] = call
        self.legs[call.caller] = call.id
        out: list[a.Action] = []
        if agent_leg is not None:
            agent_args = stasis_args(agent_leg, self.app) or []
            call.state, call.agent_leg, call.held = CallState.CONNECTED, str(agent_leg["id"]), held
            self.legs[call.agent_leg] = call.id
            agent = self.agents.get(agent_args[2] if len(agent_args) > 2 else "")
            if agent is not None:
                call.agent = agent.uid
                agent.activity = Activity.ON_HOLD if held else Activity.TALKING
                agent.call = call.id
                out += self._changed(agent)
        elif queue is not None:
            out += [
                a.StartMoh(call.id, call.bridge, queue.moh_class),
                a.SetTimer(max_wait_timer(call), queue.max_wait),
                self._queued(call, "adopted"),
            ]
        return call, out

    def _clean_bridge(
        self, bridge: dict[str, Any], present: list[dict[str, Any]], agent_legs: list[dict[str, Any]]
    ) -> list[a.Action]:
        """Hang up and delete a call a previous brain left half-done, and say it ended."""
        key = str(bridge["id"])
        call_id = str(bridge.get("name") or "")[len(BRIDGE_PREFIX) :] or key
        out: list[a.Action] = []
        for channel in present:
            role = "agent" if channel in agent_legs else "customer"
            out += [
                a.Hangup(key, str(channel["id"])),
                a.Emit(m.LEG_ENDED, {"call": call_id, "leg": str(channel["id"]), "role": role}),
            ]
        agent_args = (stasis_args(agent_legs[0], self.app) or []) if agent_legs else []
        out += [
            a.DeleteBridge(key, key),
            a.Close(key),
            a.Emit(
                m.CALL_ENDED,
                {
                    "call": call_id,
                    "result": "answered" if agent_legs else "abandoned",
                    "reason": "cleaned after the brain restarted",
                    "agent": agent_args[2] if len(agent_args) > 2 else None,
                },
            ),
        ]
        return out

    def _queued(self, call: Call, reason: str) -> a.Emit:
        """The call starts waiting in its queue (reports measure each queue's waits from here)."""
        queue = self.config.queues.get(call.queue)
        return a.Emit(
            m.CALL_QUEUED, {"call": call.id, "queue": queue.name if queue else "", "reason": reason}
        )

    def _reject(self, channel: dict[str, Any], args: list[str], reason: str) -> list[a.Action]:
        """Rule 3: a StasisStart nobody can handle is logged and hung up, never left silent."""
        cid = str(channel.get("id"))
        return [
            a.Emit(
                m.STASIS_REJECTED,
                {
                    "channel": cid,
                    "args": args,
                    "reason": reason,
                    "sip_call_id": str(channel.get("protocol_id") or ""),
                },
            ),
            a.Hangup(cid, cid),
            a.Close(cid),
        ]

    # ── agents ────────────────────────────────────────────────────────────
    def _changed(self, agent: Agent) -> list[a.Action]:
        state = agent.snapshot()
        return [a.SaveAgent(agent.uid, state), a.Emit(m.AGENT_STATE, state)]

    def _drop(self, agent: Agent) -> list[a.Action]:
        del self.agents[agent.uid]
        agent.availability, agent.activity, agent.endpoint = Availability.LOGGED_OUT, Activity.IDLE, None
        return [a.Emit(m.AGENT_STATE, agent.snapshot()), a.SaveAgent(agent.uid, None)]

    def _hold(self, call: Call, agent: Agent, hold: bool) -> list[a.Action]:
        """Hold: the agent leaves the bridge and the customer hears music. Resume: the reverse."""
        if call.held == hold:
            return []
        call.held = hold
        agent.activity = Activity.ON_HOLD if hold else Activity.TALKING
        queue = self.config.queues.get(call.queue)
        moh = queue.moh_class if queue else "default"
        leg = str(call.agent_leg)
        ari: list[a.Action] = (
            [a.RemoveFromBridge(call.id, call.bridge, leg), a.StartMoh(call.id, call.bridge, moh)]
            if hold
            else [a.StopMoh(call.id, call.bridge), a.AddToBridge(call.id, call.bridge, leg)]
        )
        event = a.Emit(m.CALL_HELD if hold else m.CALL_RESUMED, {"call": call.id, "agent": agent.uid})
        return [event, *ari, *self._changed(agent)]

    def _wrapped_up(
        self, agent: Agent, code: str, by: str, now: float, notes: str | None = None
    ) -> list[a.Action]:
        if agent.activity is not Activity.WRAPUP:
            return []
        call, agent.wrapup_call = agent.wrapup_call, None
        agent.activity, agent.idle_since = Activity.IDLE, now
        data = {"agent": agent.uid, "call": call, "code": code, "by": by}
        if notes is not None:
            data["notes"] = str(notes)[:8000]
        return [
            a.CancelTimer(wrapup_timer(agent)),
            a.Emit(m.AGENT_WRAPPED_UP, data),
            *self._changed(agent),
            *self._offer_waiting(now),
        ]

    def _refuse(self, command_id: str, kind: str, reason: str, agent: str | None = None) -> list[a.Action]:
        data = {"command_id": command_id, "type": kind, "reason": reason, "agent": agent}
        return [a.Emit(m.COMMAND_REJECTED, data)]
