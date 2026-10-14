"""Supervision (M6): a supervisor's phone listens to, whispers into or joins a connected call.

- **monitor**: a snoop on the agent's channel with ``spy=both whisper=none`` sits in a private
  bridge with the supervisor's phone. The supervisor hears the customer and the agent; nobody
  hears the supervisor.
- **whisper**: the same snoop with ``whisper=out``: what the supervisor says is mixed into what
  the agent hears, and only the agent. The customer hears nothing of it.
- **barge**: the supervisor's phone joins the call's own bridge; everyone hears everyone.

Switching mode replaces the snoop or moves the supervisor's leg; the supervisor's phone call
stays up throughout. The supervisor's leg is originated with the customer as originator, so it
belongs to the call's linkedid. Which supervisor may supervise which call is the web's check
(teams and permissions); the brain only refuses what cannot work.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from brain.core import actions as a
from brain.core.agents import Agent
from brain.core.calls import Call, CallState
from contracts import messages as m

SUPERVISE_PREFIX = "algodial-supervise-"
MODES = ("monitor", "whisper", "barge")
SUPERVISOR_COMMANDS = (m.SUPERVISE_START, m.SUPERVISE_MODE, m.SUPERVISE_STOP)


@dataclass
class Supervision:
    supervisor: str  # the supervisor's user uid
    call: str
    endpoint: str
    mode: str
    leg: str  # the supervisor's phone
    answered: bool = False
    bridge: str | None = None  # monitor / whisper: the private bridge
    snoop: str | None = None  # monitor / whisper: the snoop channel


class SupervisionMixin:
    if TYPE_CHECKING:  # what supervision uses from the Brain it is part of
        calls: dict[str, Call]
        agents: dict[str, Agent]
        legs: dict[str, str]
        supervisions: dict[str, Supervision]
        new_id: Callable[[], str]

        def _refuse(
            self, command_id: str, kind: str, reason: str, agent: str | None = None
        ) -> list[a.Action]: ...
        def _reject(self, channel: dict[str, Any], args: list[str], reason: str) -> list[a.Action]: ...

    def _supervise_command(self, kind: str, data: dict[str, Any], command_id: str) -> list[a.Action]:
        supervisor = str(data.get("supervisor") or "")
        current = self.supervisions.get(supervisor)
        if kind == m.SUPERVISE_STOP:
            if current is None:
                return []  # nothing to stop (or a repeat)
            return self._stop_supervising(current, "stopped by the supervisor")
        mode = str(data.get("mode") or "")
        if mode not in MODES:
            return self._refuse(command_id, kind, "mode is monitor, whisper or barge")
        if kind == m.SUPERVISE_MODE:
            if current is None:
                return self._refuse(command_id, kind, "the supervisor supervises nothing")
            return self._switch_mode(current, mode)

        call = self.calls.get(str(data.get("call") or ""))
        if call is None or call.state is not CallState.CONNECTED or not call.agent_leg:
            return self._refuse(command_id, kind, "no connected call with that id")
        out: list[a.Action] = []
        if current is not None:
            if current.call == call.id:
                return self._switch_mode(current, mode)
            out += self._stop_supervising(current, "moved to another call")
        supervision = Supervision(
            supervisor, call.id, str(data["endpoint"]), mode, leg="sup-" + self.new_id()
        )
        self.supervisions[supervisor] = supervision
        self.legs[supervision.leg] = call.id
        return out + [
            a.Originate(
                call.id,
                channel=supervision.leg,
                endpoint=supervision.endpoint,
                app_args=f"supervisor,{call.id},{supervisor}",
                caller_id='"Supervision" <supervision>',
                originator=call.caller,
                timeout=30,
            )
        ]

    def _supervisor_answered(self, channel: dict[str, Any], args: list[str], now: float) -> list[a.Action]:
        leg = str(channel["id"])
        supervision = self.supervisions.get(args[2])
        call = self.calls.get(args[1])
        if supervision is None or call is None or supervision.leg != leg or supervision.call != call.id:
            self.legs.pop(leg, None)
            return self._reject(channel, args, "its supervision is gone")
        supervision.answered = True
        event = a.Emit(
            m.SUPERVISE_STARTED,
            {
                "call": call.id,
                "supervisor": supervision.supervisor,
                "mode": supervision.mode,
                "agent": call.agent,
            },
        )
        return [event, *self._enter_mode(supervision, call)]

    def _snoop_started(self, channel: dict[str, Any], args: list[str]) -> list[a.Action]:
        snoop = str(channel["id"])
        supervision = self.supervisions.get(args[2])
        if supervision is None or supervision.snoop != snoop or supervision.bridge is None:
            self.legs.pop(snoop, None)
            return self._reject(channel, args, "its supervision is gone")
        return [a.AddToBridge(supervision.call, supervision.bridge, snoop)]

    def _enter_mode(self, supervision: Supervision, call: Call) -> list[a.Action]:
        if supervision.mode == "barge":
            return [a.AddToBridge(call.id, call.bridge, supervision.leg)]
        supervision.bridge = self.new_id()
        supervision.snoop = "snoop-" + self.new_id()
        self.legs[supervision.snoop] = call.id
        return [
            a.CreateBridge(call.id, supervision.bridge, SUPERVISE_PREFIX + supervision.supervisor),
            a.AddToBridge(call.id, supervision.bridge, supervision.leg),
            a.Snoop(
                call.id,
                channel=str(call.agent_leg),
                snoop_id=supervision.snoop,
                spy="both",
                whisper="out" if supervision.mode == "whisper" else "none",
                app_args=f"snoop,{call.id},{supervision.supervisor}",
            ),
        ]

    def _leave_mode(self, supervision: Supervision, call: Call) -> list[a.Action]:
        if supervision.mode == "barge":
            return [a.RemoveFromBridge(call.id, call.bridge, supervision.leg)]
        out: list[a.Action] = []
        if supervision.snoop:
            self.legs.pop(supervision.snoop, None)
            out.append(a.Hangup(call.id, supervision.snoop))
        if supervision.bridge:
            out.append(a.DeleteBridge(call.id, supervision.bridge))
        supervision.snoop = supervision.bridge = None
        return out

    def _switch_mode(self, supervision: Supervision, mode: str) -> list[a.Action]:
        call = self.calls.get(supervision.call)
        if mode == supervision.mode or call is None:
            return []
        if not supervision.answered:  # still ringing: it starts in the new mode
            supervision.mode = mode
            return []
        out = self._leave_mode(supervision, call)
        supervision.mode = mode
        out += self._enter_mode(supervision, call)
        out.append(
            a.Emit(m.SUPERVISE_CHANGED, {"call": call.id, "supervisor": supervision.supervisor, "mode": mode})
        )
        return out

    def _stop_supervising(
        self, supervision: Supervision, reason: str, hang_up_phone: bool = True
    ) -> list[a.Action]:
        self.supervisions.pop(supervision.supervisor, None)
        call = self.calls.get(supervision.call)
        out: list[a.Action] = []
        if call is not None and supervision.answered and supervision.mode != "barge":
            out += self._leave_mode(
                supervision, call
            )  # barge: hanging up the phone takes it out of the bridge
        elif supervision.snoop:
            self.legs.pop(supervision.snoop, None)
        self.legs.pop(supervision.leg, None)
        if hang_up_phone:
            out.append(a.Hangup(supervision.call, supervision.leg))
        out.append(
            a.Emit(
                m.SUPERVISE_STOPPED,
                {"call": supervision.call, "supervisor": supervision.supervisor, "reason": reason},
            )
        )
        return out

    def _supervision_leg_gone(self, call: Call, leg: str) -> list[a.Action] | None:
        """A supervisor's phone or a snoop channel ended. None: not a supervision leg."""
        for supervision in list(self.supervisions.values()):
            if supervision.call != call.id:
                continue
            if leg == supervision.leg:
                return self._stop_supervising(supervision, "the supervisor hung up", hang_up_phone=False)
            if leg == supervision.snoop:  # the agent it listened to is gone (a transfer, a hang-up)
                supervision.snoop = None
                return self._stop_supervising(supervision, "the agent's leg ended")
        return None

    def _end_supervisions(self, call: Call) -> list[a.Action]:
        out: list[a.Action] = []
        for supervision in [s for s in self.supervisions.values() if s.call == call.id]:
            out += self._stop_supervising(supervision, "the call ended")
        return out
