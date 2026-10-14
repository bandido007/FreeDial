"""Every call decision, as pure state machines.

The service hands the core one input at a time (an ARI event, a command, a timer,
a failed action) with the current time, and gets back the actions to perform
(:mod:`brain.core.actions`). Nothing here waits, sleeps or talks to anything, so
each decision is atomic and the same inputs always give the same actions.

A call to an entry point:
  customer enters Stasis (args: inbound, <entry>, <endpoint>, <linkedid>)
    -> answer, own mixing bridge with hold music, max-wait timer
    -> ring the longest-idle Ready agent of the entry's queue (args: agent, <call>, <agent>)
  the agent's phone rejects, rings out or is offline
    -> the next agent; the one who rang out becomes Not responding
  the agent answers -> music off, agent into the bridge
  either side leaves -> every leg hung up and the bridge deleted (rule 8)
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from brain.core import actions as a
from brain.core.agents import Activity, Agent, Availability
from brain.core.config import Config, QueueConfig
from contracts import messages as m

BRIDGE_PREFIX = "algodial-call-"
AGENT_LEG_PREFIX = "agent-"

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
    WAITING = "waiting"  # in the bridge with music, no agent ringing
    OFFERING = "offering"  # an agent's phone is ringing
    CONNECTED = "connected"
    ENDED = "ended"


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
    offer: str | None = None  # the ringing agent leg
    offer_agent: str | None = None
    agent_leg: str | None = None
    agent: str | None = None
    tried: set[str] = field(default_factory=set)  # agents who missed or refused it this round
    retry_pending: bool = False
    held: bool = False  # the agent put the customer on hold: agent out of the bridge, music on
    caller_number: str = ""
    caller_name: str = ""


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


class Brain:
    def __init__(self, config: Config, *, app: str, new_id: Callable[[], str]):
        self.app = app
        self.new_id = new_id
        self.config = Config()
        self.agents: dict[str, Agent] = {}
        self.calls: dict[str, Call] = {}
        self.legs: dict[str, str] = {}  # channel id -> call id
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
            return self._reject(channel, args, "unknown args")
        if kind in ("StasisEnd", "ChannelDestroyed"):
            return self._leg_gone(str(channel.get("id")), event.get("cause"), now)
        return []

    def on_timer(self, key: str, now: float) -> list[a.Action]:
        kind, _, call_id = key.partition(":")
        if kind == "wrapup":
            agent = self.agents.get(call_id)
            return self._wrapped_up(agent, "", "timeout", now) if agent else []
        call = self.calls.get(call_id)
        if call is None:
            return []
        if kind == "maxwait" and call.state in (CallState.WAITING, CallState.OFFERING):
            return self._end(call, "overflow", "max wait reached", now)
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
            if action.channel != call.offer:
                return []
            return self._offer_failed(call, "offline", None, now)
        return self._end(call, "failed", f"{type(action).__name__} failed: {error}", now)

    def on_command(self, kind: str, data: dict[str, Any], command_id: str, now: float) -> list[a.Action]:
        """Agent requests. Each is idempotent: asking twice changes nothing the second time (rule 12)."""
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
            return self._wrapped_up(agent, str(data.get("code") or ""), "agent", now)

        if kind in (m.CALL_HOLD, m.CALL_RESUME):
            call = self.calls.get(agent.call or "")
            if call is None or call.state is not CallState.CONNECTED or call.agent != agent.uid:
                return self._refuse(command_id, kind, "the agent has no connected call", agent.uid)
            return self._hold(call, agent, kind == m.CALL_HOLD)

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
            elif call.offer and call.offer not in live:
                out += self._offer_failed(call, "failed", None, now)

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
        for bridge in bridges:
            if not str(bridge.get("name") or "").startswith(BRIDGE_PREFIX) or bridge["id"] in owned:
                continue
            present = [live[str(cid)] for cid in bridge.get("channels") or [] if str(cid) in live]
            callers = [ch for ch in present if (stasis_args(ch, self.app) or [""])[0] == "inbound"]
            agents = [ch for ch in present if (stasis_args(ch, self.app) or [""])[0] == "agent"]
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
        queue = self.config.queues.get(self.config.entries.get(entry, ""))
        if queue is None:
            return self._reject(channel, args, f"no entry point {entry}")
        if call_id in self.calls:
            return []

        call = Call(
            id=call_id,
            caller=str(channel["id"]),
            entry=entry,
            queue=queue.uid,
            bridge=self.new_id(),
            caller_id=caller_id(channel),
            queued_at=now,
        )
        self.calls[call.id] = call
        self.legs[call.caller] = call.id
        caller = channel.get("caller") or {}
        call.caller_number = endpoint or str(caller.get("number") or "")
        call.caller_name = str(caller.get("name") or "")
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
                },
            ),
            a.Answer(call.id, call.caller),
            a.CreateBridge(call.id, call.bridge, BRIDGE_PREFIX + call.id),
            a.AddToBridge(call.id, call.bridge, call.caller),
            a.StartMoh(call.id, call.bridge, queue.moh_class),
            a.SetTimer(max_wait_timer(call), queue.max_wait),
        ]
        return out + self._hunt(call, now)

    def _hunt(self, call: Call, now: float) -> list[a.Action]:
        """Offer the call to the longest-idle free agent of its queue who has not refused it yet."""
        if call.state is not CallState.WAITING:
            return []
        queue = self.config.queues.get(call.queue)
        if queue is None:
            return []
        free = [
            self.agents[uid]
            for uid in queue.members
            if uid in self.agents and self.agents[uid].can_take_a_call
        ]
        fresh = [agent for agent in free if agent.uid not in call.tried]
        if fresh:
            return self._offer(call, min(fresh, key=lambda agent: (agent.idle_since, agent.number)), queue)
        if free and not call.retry_pending:  # every free agent refused it: ask them again after a pause
            call.retry_pending = True
            return [a.SetTimer(retry_timer(call), queue.retry_delay)]
        return []

    def _offer(self, call: Call, agent: Agent, queue: QueueConfig) -> list[a.Action]:
        leg = AGENT_LEG_PREFIX + self.new_id()
        call.state, call.offer, call.offer_agent = CallState.OFFERING, leg, agent.uid
        self.legs[leg] = call.id
        agent.activity, agent.call = Activity.OFFERED, call.id
        return [
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

    def _offer_waiting(self, now: float) -> list[a.Action]:
        out: list[a.Action] = []
        for call in sorted(self.calls.values(), key=lambda call: call.queued_at):
            out += self._hunt(call, now)
        return out

    def _agent_answered(self, channel: dict[str, Any], args: list[str], now: float) -> list[a.Action]:
        leg = str(channel["id"])
        call = self.calls.get(args[1])
        if call is None or call.offer != leg:
            self.legs.pop(leg, None)
            return self._reject(channel, args, "its call is gone")

        agent = self.agents.get(str(call.offer_agent))
        call.state, call.agent_leg, call.agent = CallState.CONNECTED, leg, call.offer_agent
        call.offer = call.offer_agent = None
        out: list[a.Action] = [
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
        if agent is not None:
            agent.activity, agent.reason = Activity.TALKING, ""
            out += self._changed(agent)
        return out

    def _leg_gone(self, leg: str, cause: Any, now: float) -> list[a.Action]:
        call = self.calls.get(self.legs.get(leg, ""))
        if call is None:
            self.legs.pop(leg, None)
            return []
        if leg == call.caller:
            return self._end(call, "answered" if call.agent else "abandoned", "customer hung up", now)
        if leg == call.agent_leg:
            return self._end(call, "answered", "agent hung up", now)
        if leg == call.offer:
            reason = OFFER_FAILURES.get(cause, "failed") if isinstance(cause, int) else "failed"
            return self._offer_failed(call, reason, cause, now)
        self.legs.pop(leg, None)
        return []

    def _offer_failed(self, call: Call, reason: str, cause: Any, now: float) -> list[a.Action]:
        leg, agent = str(call.offer), self.agents.get(str(call.offer_agent))
        self.legs.pop(leg, None)
        call.state, call.offer, call.offer_agent = CallState.WAITING, None, None
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
                if reason in NOT_RESPONDING:
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
        for leg, role in ((call.offer, "agent"), (call.agent_leg, "agent"), (call.caller, "customer")):
            if leg:
                self.legs.pop(leg, None)
                out += [
                    a.Hangup(call.id, leg),
                    a.Emit(m.LEG_ENDED, {"call": call.id, "leg": leg, "role": role}),
                ]
        out += [
            a.DeleteBridge(call.id, call.bridge),
            a.Close(call.id),
            a.Emit(m.CALL_ENDED, {"call": call.id, "result": result, "reason": reason, "agent": call.agent}),
        ]
        del self.calls[call.id]

        queue = self.config.queues.get(call.queue)
        wrapup = queue.wrapup_seconds if queue else 0
        for uid, talked in ((call.agent, True), (call.offer_agent, False)):
            agent = self.agents.get(uid or "")
            if agent is None or agent.call != call.id:
                continue
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
        return out + self._offer_waiting(now)

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
        queue_uid = self.config.entries.get(entry, "")
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

    def _wrapped_up(self, agent: Agent, code: str, by: str, now: float) -> list[a.Action]:
        if agent.activity is not Activity.WRAPUP:
            return []
        call, agent.wrapup_call = agent.wrapup_call, None
        agent.activity, agent.idle_since = Activity.IDLE, now
        return [
            a.CancelTimer(wrapup_timer(agent)),
            a.Emit(m.AGENT_WRAPPED_UP, {"agent": agent.uid, "call": call, "code": code, "by": by}),
            *self._changed(agent),
            *self._offer_waiting(now),
        ]

    def _refuse(self, command_id: str, kind: str, reason: str, agent: str | None = None) -> list[a.Action]:
        data = {"command_id": command_id, "type": kind, "reason": reason, "agent": agent}
        return [a.Emit(m.COMMAND_REJECTED, data)]
