"""Transfers and conference (M5): the agent on a call hands it on, or brings someone in.

- **Blind** to an agent or a number: the agent leaves at once, the customer hears music while
  the target rings, and is connected when the target answers. If nobody answers, the call goes
  back to its queue: the customer is never lost. **Blind to a queue**: the call re-enters that
  queue's routing, optionally with extra required skills (ROUTING_DESIGN.md, "Transfer").
- **Consult** an agent or a number: the customer is put on hold, the agent and the target talk
  in a private bridge. Then *complete* (the target takes the customer, the agent leaves),
  *cancel* (the agent goes back to the customer) or *conference* (all three together).
- In a conference the agent can drop the other party, or leave them with the customer.
- Hang-ups mid-way do what a phone user expects: the agent hanging up while consulting completes
  the transfer, the consulted party hanging up cancels it.

Every target leg is originated with the customer's channel as originator (rule 4), so CEL
shows one linkedid for the whole call.
"""

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from brain.core import actions as a
from brain.core.agents import Activity, Agent, Availability
from brain.core.calls import (
    AGENT_LEG_PREFIX,
    CONSULT_PREFIX,
    NOT_RESPONDING,
    OFFER_FAILURES,
    Call,
    CallState,
    Transfer,
    max_wait_timer,
)
from brain.core.config import Config, QueueConfig, Requirement
from contracts import messages as m

NUMBER = re.compile(r"^[A-Za-z0-9+*#._-]{1,40}$")


class TransferMixin:
    if TYPE_CHECKING:  # what transfers use from the Brain they are part of
        config: Config
        calls: dict[str, Call]
        agents: dict[str, Agent]
        legs: dict[str, str]
        new_id: Callable[[], str]

        def _refuse(
            self, command_id: str, kind: str, reason: str, agent: str | None = None
        ) -> list[a.Action]: ...
        def _reject(self, channel: dict[str, Any], args: list[str], reason: str) -> list[a.Action]: ...
        def _changed(self, agent: Agent) -> list[a.Action]: ...
        def _release(self, uid: str | None, call: Call, talked: bool, now: float) -> list[a.Action]: ...
        def _hunt(self, call: Call, now: float) -> list[a.Action]: ...
        def _priority(self, call: Call) -> int: ...
        def _move(
            self, call: Call, queue: QueueConfig, target: QueueConfig, reason: str, now: float
        ) -> list[a.Action]: ...

    # ── commands ──────────────────────────────────────────────────────────
    def _transfer_command(
        self, kind: str, data: dict[str, Any], command_id: str, agent: Agent, now: float
    ) -> list[a.Action]:
        call = self.calls.get(agent.call or "")
        on_call = call is not None and call.state is CallState.CONNECTED

        if kind == m.CALL_TRANSFER:
            if not on_call or call is None or call.agent != agent.uid:
                return self._refuse(command_id, kind, "the agent has no connected call", agent.uid)
            if call.transfer is not None or call.third is not None:
                return self._refuse(command_id, kind, "a transfer or conference is under way", agent.uid)
            if call.held:
                return self._refuse(command_id, kind, "resume the call first", agent.uid)
            return self._start_transfer(call, agent, data, command_id, now)

        transfer = call.transfer if call is not None else None
        if kind in (m.TRANSFER_COMPLETE, m.TRANSFER_CANCEL, m.CONFERENCE_START):
            if call is None or transfer is None or transfer.by != agent.uid or transfer.kind != "consult":
                return self._refuse(command_id, kind, "the agent has no consult under way", agent.uid)
            if kind == m.TRANSFER_CANCEL:
                return self._cancel_transfer(call, "cancelled by the agent", now)
            if transfer.state != "consulting":
                return self._refuse(command_id, kind, "the consulted party has not answered yet", agent.uid)
            if kind == m.TRANSFER_COMPLETE:
                return self._complete_consult(call, now)
            return self._start_conference(call, now)

        # CONFERENCE_DROP
        if call is None or call.third is None or call.agent != agent.uid:
            return self._refuse(command_id, kind, "the agent is in no conference", agent.uid)
        if data.get("who") == "me":
            return self._agent_left(call, now)
        return self._third_left(call, "dropped by the agent", now, hang_up=True)

    def _start_transfer(
        self, call: Call, agent: Agent, data: dict[str, Any], command_id: str, now: float
    ) -> list[a.Action]:
        kind, target_kind, target = str(data["kind"]), str(data["target_kind"]), str(data["target"])
        refuse = lambda reason: self._refuse(command_id, m.CALL_TRANSFER, reason, agent.uid)  # noqa: E731
        if kind not in ("blind", "consult"):
            return refuse("a transfer is blind or consult")
        if target_kind == "ai":
            return refuse("transfers to an AI agent come with M8")
        if target_kind == "queue":
            if kind == "consult":
                return refuse("a consult goes to an agent or a number")
            queue = self._queue_named(target)
            if queue is None:
                return refuse("unknown queue")
            skills = {str(name): int(level) for name, level in (data.get("skills") or {}).items()}
            if any(name not in self.config.skills for name in skills):
                return refuse("unknown skill")
            return self._blind_to_queue(call, agent, queue, skills, now)
        if target_kind == "agent":
            other = self.agents.get(target)
            free = (
                other is not None
                and other.uid != agent.uid
                and not other.removed
                and other.endpoint is not None
                and other.availability is not Availability.LOGGED_OUT
                and other.activity in (Activity.IDLE, Activity.WRAPUP)
                and other.call is None
            )
            if not free or other is None:
                return refuse("the target agent is not free")
            return self._ring_target(call, agent, kind, "agent", other.uid, str(other.endpoint), other, now)
        if target_kind == "number":
            if not NUMBER.match(target):
                return refuse("not a dialable number")
            endpoint = self.config.dial_template.format(number=target)
            return self._ring_target(call, agent, kind, "number", target, endpoint, None, now)
        return refuse("a transfer goes to an agent, a queue or a number")

    # ── starting ──────────────────────────────────────────────────────────
    def _started(self, call: Call, transfer: Transfer, shown_target: str) -> a.Emit:
        return a.Emit(
            m.TRANSFER_STARTED,
            {
                "call": call.id,
                "transfer": transfer.id,
                "kind": transfer.kind,
                "target_kind": transfer.target_kind,
                "target": shown_target,
                "agent": transfer.by,
                "target_agent": transfer.target_agent,
            },
        )

    def _blind_to_queue(
        self, call: Call, agent: Agent, queue: QueueConfig, skills: dict[str, int], now: float
    ) -> list[a.Action]:
        transfer = Transfer(self.new_id(), "blind", "queue", queue.name, agent.uid)
        out: list[a.Action] = [self._started(call, transfer, queue.name)]
        out += self._agent_hangs_up(call, now)
        call.state, call.held = CallState.WAITING, False
        call.extra = tuple(Requirement(name, level) for name, level in skills.items())
        call.ticket_source = "transfer"
        out += [
            a.StartMoh(call.id, call.bridge, queue.moh_class),
            a.Emit(
                m.ROUTING_TICKET,
                {
                    "call": call.id,
                    "queue": queue.name,
                    "language": call.language,
                    "priority": self._priority(call),
                    "skills": skills,
                    "source": "transfer",
                },
            ),
            a.Emit(m.TRANSFER_COMPLETED, {"call": call.id, "transfer": transfer.id, "agent": agent.uid}),
        ]
        current = self.config.queues.get(call.queue)
        if current is not None and queue.uid != call.queue:
            return out + self._move(call, current, queue, "routing", now)
        call.queue_entered_at = now
        call.tried.clear()
        return out + [a.SetTimer(max_wait_timer(call), queue.max_wait)] + self._hunt(call, now)

    def _ring_target(
        self,
        call: Call,
        agent: Agent,
        kind: str,
        target_kind: str,
        target: str,
        endpoint: str,
        other: Agent | None,
        now: float,
    ) -> list[a.Action]:
        transfer = Transfer(
            id=self.new_id(),
            kind=kind,
            target_kind=target_kind,
            target=target,
            by=agent.uid,
            leg=AGENT_LEG_PREFIX + self.new_id(),
            target_agent=other.uid if other else None,
        )
        call.transfer = transfer
        self.legs[str(transfer.leg)] = call.id
        queue = self.config.queues.get(call.queue)
        out: list[a.Action] = [self._started(call, transfer, other.number if other else target)]
        if kind == "blind":
            out += self._agent_hangs_up(call, now)
            out.append(a.StartMoh(call.id, call.bridge, queue.moh_class if queue else "default"))
        else:  # consult: the customer waits on hold while the agent talks to the target
            call.held = True
            agent.activity = Activity.ON_HOLD
            out += [
                a.RemoveFromBridge(call.id, call.bridge, str(call.agent_leg)),
                a.StartMoh(call.id, call.bridge, queue.moh_class if queue else "default"),
                *self._changed(agent),
            ]
        out.append(
            a.Originate(
                call.id,
                channel=str(transfer.leg),
                endpoint=endpoint,
                app_args=f"transfer,{call.id},{transfer.id}",
                caller_id=call.caller_id,
                originator=call.caller,
                timeout=queue.ring_timeout if queue else 20,
            )
        )
        if other is not None:
            if other.activity is Activity.WRAPUP:
                out.append(a.CancelTimer(f"wrapup:{other.uid}"))
                other.wrapup_call = None
            other.activity, other.call = Activity.OFFERED, call.id
            out += self._changed(other)
        return out

    # ── the target answers ────────────────────────────────────────────────
    def _transfer_answered(self, channel: dict[str, Any], args: list[str], now: float) -> list[a.Action]:
        leg = str(channel["id"])
        call = self.calls.get(args[1])
        transfer = call.transfer if call is not None else None
        if call is None or transfer is None or transfer.id != args[2] or transfer.leg != leg:
            self.legs.pop(leg, None)
            return self._reject(channel, args, "its transfer is gone")
        out: list[a.Action] = [
            a.Emit(
                m.TRANSFER_ANSWERED,
                {
                    "call": call.id,
                    "transfer": transfer.id,
                    "leg": leg,
                    "agent": transfer.by,
                    "target_agent": transfer.target_agent,
                },
            )
        ]
        target = self.agents.get(transfer.target_agent or "")
        if target is not None:
            target.activity, target.reason = Activity.TALKING, ""
            out += self._changed(target)
        if call.agent_leg is None:  # blind (or a consult whose agent hung up): straight to the customer
            return out + self._hand_over(call, now)
        transfer.state, transfer.bridge = "consulting", self.new_id()
        return out + [
            a.CreateBridge(call.id, transfer.bridge, CONSULT_PREFIX + call.id),
            a.AddToBridge(call.id, transfer.bridge, str(call.agent_leg)),
            a.AddToBridge(call.id, transfer.bridge, leg),
        ]

    def _hand_over(self, call: Call, now: float) -> list[a.Action]:
        """The target takes the customer: into the customer's bridge, music off. The transfer is done."""
        transfer = call.transfer
        assert transfer is not None and transfer.leg is not None
        out: list[a.Action] = []
        if transfer.bridge:
            out.append(a.DeleteBridge(call.id, transfer.bridge))
        out += [
            a.StopMoh(call.id, call.bridge),
            a.AddToBridge(call.id, call.bridge, transfer.leg),
            a.Emit(m.TRANSFER_COMPLETED, {"call": call.id, "transfer": transfer.id, "agent": transfer.by}),
        ]
        call.agent_leg, call.agent, call.held = transfer.leg, transfer.target_agent, False
        call.transfer = None
        call.connected_at = call.connected_at or now
        if transfer.target_agent:
            out.append(
                a.Emit(
                    m.CALL_CONNECTED, {"call": call.id, "leg": transfer.leg, "agent": transfer.target_agent}
                )
            )
        return out

    # ── consult: complete, cancel, conference ─────────────────────────────
    def _complete_consult(self, call: Call, now: float) -> list[a.Action]:
        out = self._agent_hangs_up(call, now)
        return out + self._hand_over(call, now)

    def _cancel_transfer(self, call: Call, reason: str, now: float) -> list[a.Action]:
        transfer = call.transfer
        assert transfer is not None
        out: list[a.Action] = []
        if transfer.bridge:
            out.append(a.DeleteBridge(call.id, transfer.bridge))
        if transfer.leg:
            self.legs.pop(transfer.leg, None)
            out += [
                a.Hangup(call.id, transfer.leg),
                a.Emit(
                    m.LEG_ENDED,
                    {
                        "call": call.id,
                        "leg": transfer.leg,
                        "role": "agent" if transfer.target_agent else "external",
                    },
                ),
            ]
        out.append(
            a.Emit(
                m.TRANSFER_CANCELLED,
                {"call": call.id, "transfer": transfer.id, "reason": reason, "agent": transfer.by},
            )
        )
        call.transfer = None
        out += self._release(transfer.target_agent, call, transfer.state == "consulting", now)
        return out + self._back_to_customer(call)

    def _back_to_customer(self, call: Call) -> list[a.Action]:
        """The agent who was consulting rejoins the customer, music off."""
        call.held = False
        agent = self.agents.get(call.agent or "")
        out: list[a.Action] = [
            a.StopMoh(call.id, call.bridge),
            a.AddToBridge(call.id, call.bridge, str(call.agent_leg)),
        ]
        if agent is not None and agent.call == call.id:
            agent.activity = Activity.TALKING
            out += self._changed(agent)
        return out

    def _start_conference(self, call: Call, now: float) -> list[a.Action]:
        transfer = call.transfer
        assert transfer is not None and transfer.leg is not None
        call.third, call.transfer, call.held = (transfer.leg, transfer.target_agent), None, False
        out: list[a.Action] = [
            a.DeleteBridge(call.id, str(transfer.bridge)),
            a.StopMoh(call.id, call.bridge),
            a.AddToBridge(call.id, call.bridge, str(call.agent_leg)),
            a.AddToBridge(call.id, call.bridge, transfer.leg),
            a.Emit(
                m.CONFERENCE_STARTED,
                {
                    "call": call.id,
                    "transfer": transfer.id,
                    "legs": [call.caller, call.agent_leg, transfer.leg],
                    "agent": transfer.by,
                    "target_agent": transfer.target_agent,
                },
            ),
        ]
        agent = self.agents.get(call.agent or "")
        if agent is not None:
            agent.activity = Activity.TALKING
            out += self._changed(agent)
        return out

    # ── people leaving ────────────────────────────────────────────────────
    def _agent_hangs_up(self, call: Call, now: float) -> list[a.Action]:
        """The agent's leg is hung up by the brain (blind transfer, complete, leaving a conference)."""
        leg, uid = call.agent_leg, call.agent
        call.agent_leg = call.agent = None
        out: list[a.Action] = []
        if leg:
            self.legs.pop(leg, None)
            out += [
                a.Hangup(call.id, leg),
                a.Emit(m.LEG_ENDED, {"call": call.id, "leg": leg, "role": "agent"}),
            ]
        return out + self._release(uid, call, True, now)

    def _agent_left(self, call: Call, now: float) -> list[a.Action]:
        """The agent's leg is gone while a transfer or conference is under way."""
        transfer = call.transfer
        if call.third is not None:  # the conference goes on without the agent: the third party takes over
            leg, uid = call.agent_leg, call.agent
            out = self._agent_hangs_up(call, now)
            call.agent_leg, call.agent = call.third
            call.third = None
            return out + [
                a.Emit(
                    m.CALL_PARTY_LEFT,
                    {"call": call.id, "leg": leg, "agent": uid, "reason": "left the conference"},
                )
            ]
        assert transfer is not None
        out = self._agent_hangs_up(call, now)
        if transfer.state == "consulting":  # hanging up mid-consult completes it, as on a desk phone
            return out + self._hand_over(call, now)
        transfer.kind = "blind"  # still ringing: the target gets the customer when they answer
        return out

    def _third_left(self, call: Call, reason: str, now: float, hang_up: bool = False) -> list[a.Action]:
        assert call.third is not None
        leg, uid = call.third
        call.third = None
        self.legs.pop(leg, None)
        out: list[a.Action] = [a.Hangup(call.id, leg)] if hang_up else []
        out += [
            a.Emit(m.LEG_ENDED, {"call": call.id, "leg": leg, "role": "agent" if uid else "external"}),
            a.Emit(m.CALL_PARTY_LEFT, {"call": call.id, "leg": leg, "agent": uid, "reason": reason}),
        ]
        return out + self._release(uid, call, True, now)

    def _transfer_leg_gone(self, call: Call, cause: Any, now: float) -> list[a.Action]:
        """The target's channel ended: before answering (a failed transfer) or during a consult."""
        transfer = call.transfer
        assert transfer is not None and transfer.leg is not None
        self.legs.pop(transfer.leg, None)
        if transfer.state == "consulting":
            transfer.leg = None
            return self._cancel_transfer(call, "the other party hung up", now)
        reason = OFFER_FAILURES.get(cause, "failed") if isinstance(cause, int) else "failed"
        call.transfer = None
        out: list[a.Action] = [
            a.Emit(
                m.TRANSFER_FAILED,
                {"call": call.id, "transfer": transfer.id, "reason": reason, "agent": transfer.by},
            ),
            a.Emit(
                m.LEG_ENDED,
                {
                    "call": call.id,
                    "leg": transfer.leg,
                    "role": "agent" if transfer.target_agent else "external",
                    "cause": cause,
                },
            ),
        ]
        target = self.agents.get(transfer.target_agent or "")
        if target is not None and target.call == call.id:
            target.call = None
            target.activity = Activity.NOT_RESPONDING if reason in NOT_RESPONDING else Activity.IDLE
            target.reason = reason if reason in NOT_RESPONDING else target.reason
            out += self._changed(target)
        if call.agent_leg is not None:  # consult: the agent goes back to the customer
            return out + self._back_to_customer(call)
        # blind: nobody is with the customer any more. Back into the queue: the customer is not lost.
        queue = self.config.queues.get(call.queue)
        call.state, call.held = CallState.WAITING, False
        call.queue_entered_at = now
        call.tried.clear()
        out.append(a.SetTimer(max_wait_timer(call), queue.max_wait if queue else 300))
        return out + self._hunt(call, now)

    def _queue_named(self, name: str) -> QueueConfig | None:
        wanted = name.strip().lower()
        return next(
            (
                queue
                for queue in self.config.queues.values()
                if queue.name.lower() == wanted or queue.uid == name
            ),
            None,
        )
