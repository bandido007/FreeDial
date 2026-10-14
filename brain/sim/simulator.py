"""A virtual contact centre on a virtual clock, driving the real brain core.

The simulator plays Asterisk and the people: customers arrive and wait (and give
up when their patience runs out), agents' phones ring and the agents answer,
reject, ignore or are offline, conversations last as long as scripted, timers
fire. Nothing waits for real time, so an hour of traffic runs in milliseconds and
the same scenario always gives the same result.

    sim = Simulation(
        queues=[QueueConfig(uid="support", name="Support", strategy="round_robin")],
        agents=[VirtualAgent("7001"), VirtualAgent("7002", skills={"swahili": 8})],
        calls=[VirtualCall(at=0), VirtualCall(at=5, talk=60)],
    )
    report = sim.run()
    print(report.table())
"""

import heapq
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from brain.core import actions as a
from brain.core.brain import Brain
from brain.core.config import AgentConfig, Config, EntryConfig, QueueConfig
from contracts import messages as m

ENDPOINT = "SIM/{number}"


@dataclass
class VirtualAgent:
    number: str
    skills: dict[str, int] = field(default_factory=dict)
    queues: tuple[str, ...] = ("support",)
    behaviour: str = "answer"  # answer | reject | ignore | offline
    answer_after: float = 2.0  # seconds the phone rings before they pick up (or reject)
    ready_at: float = 0.0
    wrapup_for: float = 5.0  # seconds of wrap-up before pressing Done (when the queue wraps up)

    @property
    def uid(self) -> str:
        return f"agent-{self.number}"


@dataclass
class VirtualCall:
    at: float
    entry: str = "800"
    talk: float = 60.0  # seconds of conversation once answered
    patience: float = 3600.0  # seconds the customer waits before hanging up


@dataclass
class CallRecord:
    id: str
    arrived: float
    queue: str = ""
    answered_at: float | None = None
    ended_at: float | None = None
    result: str = ""
    agent: str | None = None
    rung: list[str] = field(default_factory=list)  # agent numbers, in offer order

    @property
    def waited(self) -> float | None:
        return None if self.answered_at is None else self.answered_at - self.arrived


@dataclass
class AgentRecord:
    number: str
    offered: int = 0
    answered: int = 0
    refused: int = 0  # rejected, rang out, offline
    talk_time: float = 0.0


@dataclass
class Report:
    calls: list[CallRecord]
    agents: dict[str, AgentRecord]
    events: list[tuple[float, str, dict[str, Any]]]
    ended_at: float

    def answered_by(self) -> dict[str, int]:
        return {number: record.answered for number, record in self.agents.items()}

    def service_level(self, seconds: float) -> float:
        """Percent of calls answered within ``seconds`` of arriving, of all calls that arrived."""
        if not self.calls:
            return 100.0
        within = sum(1 for call in self.calls if call.waited is not None and call.waited <= seconds)
        return 100.0 * within / len(self.calls)

    def results(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for call in self.calls:
            out[call.result or "open"] = out.get(call.result or "open", 0) + 1
        return out

    def table(self, service_seconds: float = 20) -> str:
        waits = [call.waited for call in self.calls if call.waited is not None]
        lines = [
            f"{len(self.calls)} calls over {self.ended_at / 60:.1f} min: {self.results()}",
            f"service level {self.service_level(service_seconds):.0f}% answered within {service_seconds:g}s; "
            f"average wait {sum(waits) / len(waits) if waits else 0:.1f}s, longest {max(waits, default=0):.1f}s",
            f"{'agent':<8}{'offered':>9}{'answered':>10}{'refused':>9}{'talk min':>10}",
        ]
        for number, record in sorted(self.agents.items()):
            lines.append(
                f"{number:<8}{record.offered:>9}{record.answered:>10}{record.refused:>9}{record.talk_time / 60:>10.1f}"
            )
        return "\n".join(lines)


class Simulation:
    def __init__(
        self,
        *,
        queues: list[QueueConfig],
        agents: list[VirtualAgent],
        calls: list[VirtualCall],
        entries: dict[str, tuple[str, str | None]] | None = None,  # number -> (queue uid, language)
    ):
        self.people = {agent.uid: agent for agent in agents}
        by_endpoint = {ENDPOINT.format(number=agent.number): agent for agent in agents}
        self.by_endpoint = by_endpoint
        config = Config(
            agents={
                agent.uid: AgentConfig(agent.uid, agent.number, f"Agent {agent.number}", dict(agent.skills))
                for agent in agents
            },
            queues={
                queue.uid: replace(
                    queue, members=tuple(agent.uid for agent in agents if queue.uid in agent.queues)
                )
                for queue in queues
            },
            entries={
                number: EntryConfig(number, queue, language)
                for number, (queue, language) in (entries or {"800": (queues[0].uid, None)}).items()
            },
        )
        self.queue_names = {queue.uid: queue.name for queue in queues}
        ids = itertools.count(1)
        self.core = Brain(config, app="algodial", new_id=lambda: f"id{next(ids)}")
        self.calls_planned = sorted(calls, key=lambda call: call.at)
        self.now = 0.0
        self._heap: list[tuple[float, int, Callable[..., None], tuple[Any, ...]]] = []
        self._seq = itertools.count()
        self._cancelled: set[int] = set()
        self._timers: dict[str, int] = {}
        self._channels: dict[str, dict[str, Any]] = {}  # live channels: kind, call, agent, tokens
        self.records: dict[str, CallRecord] = {}
        self.agent_records = {agent.number: AgentRecord(agent.number) for agent in agents}
        self.events: list[tuple[float, str, dict[str, Any]]] = []
        self.talking: dict[str, str] = {}  # agent uid -> call: an agent is never on two calls

    # ── the clock ─────────────────────────────────────────────────────────
    def at(self, when: float, action: Callable[..., None], *args: Any) -> int:
        token = next(self._seq)
        heapq.heappush(self._heap, (when, token, action, args))
        return token

    def cancel(self, token: int | None) -> None:
        if token is not None:
            self._cancelled.add(token)

    def run(self, until: float | None = None) -> Report:
        for agent in self.people.values():
            self.at(0.0, self._login, agent)
            self.at(agent.ready_at, self._command, m.AGENT_READY, {"agent": agent.uid})
        for number, call in enumerate(self.calls_planned, start=1):
            self.at(call.at, self._arrive, f"c{number}", call)
        while self._heap:
            when, token, action, args = heapq.heappop(self._heap)
            if until is not None and when > until:
                break
            if token in self._cancelled:
                continue
            self.now = when
            action(*args)
        return Report(list(self.records.values()), self.agent_records, self.events, self.now)

    # ── what the brain asks for ───────────────────────────────────────────
    def carry_out(self, actions: list[a.Action]) -> None:
        for action in actions:
            match action:
                case a.Emit():
                    self._observe(action.type, action.data)
                case a.Originate():
                    self._ring(action)
                case a.Hangup(channel=channel):
                    self._drop(channel, notify=True)
                case a.SetTimer(key=key, seconds=seconds):
                    self.cancel(self._timers.get(key))
                    self._timers[key] = self.at(self.now + seconds, self._timer, key)
                case a.CancelTimer(key=key):
                    self.cancel(self._timers.pop(key, None))
                case _:
                    pass  # bridges, music, answering the customer, saving state: nothing to simulate

    def _command(self, kind: str, data: dict[str, Any]) -> None:
        self.carry_out(self.core.on_command(kind, data, f"cmd-{next(self._seq)}", self.now))

    def _timer(self, key: str) -> None:
        self._timers.pop(key, None)
        self.carry_out(self.core.on_timer(key, self.now))

    def _event(self, event: dict[str, Any]) -> None:
        self.carry_out(self.core.on_ari(event, self.now))

    # ── people ────────────────────────────────────────────────────────────
    def _login(self, agent: VirtualAgent) -> None:
        self._command(m.AGENT_LOGIN, {"agent": agent.uid, "endpoint": ENDPOINT.format(number=agent.number)})

    def _arrive(self, cid: str, plan: VirtualCall) -> None:
        self.records[cid] = CallRecord(id=cid, arrived=self.now)
        channel = self._channel(
            cid, f"PJSIP/customer-{cid}", "Stasis", f"algodial,inbound,{plan.entry},{cid},{cid}"
        )
        give_up = self.at(self.now + plan.patience, self._customer_leaves, cid)
        self._channels[cid] = {"kind": "customer", "plan": plan, "tokens": [give_up]}
        self._event({"type": "StasisStart", "args": ["inbound", plan.entry, cid, cid], "channel": channel})

    def _customer_leaves(self, cid: str) -> None:
        if self._drop(cid, notify=False):
            self._event({"type": "StasisEnd", "channel": {"id": cid}})

    def _ring(self, originate: a.Originate) -> None:
        agent = self.by_endpoint.get(originate.endpoint)
        record = self.agent_records[agent.number] if agent else None
        if record:
            record.offered += 1
        if agent is None or agent.behaviour == "offline":
            self.at(self.now, self._failed, originate)
            return
        leg = originate.channel
        self._channels[leg] = {
            "kind": "agent",
            "call": originate.call,
            "agent": agent,
            "ringing": True,
            "tokens": [],
        }
        if agent.behaviour == "answer" and agent.answer_after < originate.timeout:
            token = self.at(self.now + agent.answer_after, self._pick_up, leg, originate.app_args)
        elif agent.behaviour == "reject":
            token = self.at(self.now + min(agent.answer_after, originate.timeout), self._destroyed, leg, 17)
        else:  # ignores it, or too slow: rings out
            token = self.at(self.now + originate.timeout, self._destroyed, leg, 19)
        self._channels[leg]["tokens"].append(token)

    def _failed(self, originate: a.Originate) -> None:
        self.carry_out(self.core.on_failed(originate, '{"error": "Allocation failed"}', self.now))

    def _pick_up(self, leg: str, app_args: str) -> None:
        channel = self._channels.get(leg)
        if channel is None or not channel["ringing"]:
            return
        channel["ringing"] = False
        args = app_args.split(",")
        self._event(
            {
                "type": "StasisStart",
                "args": args,
                "channel": self._channel(leg, "SIM/agent", "Stasis", "algodial," + app_args),
            }
        )

    def _destroyed(self, leg: str, cause: int) -> None:
        if self._drop(leg, notify=False):
            self._event({"type": "ChannelDestroyed", "cause": cause, "channel": {"id": leg}})

    def _drop(self, cid: str, *, notify: bool) -> bool:
        """The channel goes. ``notify``: the brain hung it up, so Asterisk tells it the channel is gone."""
        channel = self._channels.pop(cid, None)
        if channel is None:
            return False
        for token in channel["tokens"]:
            self.cancel(token)
        if notify and channel["kind"] == "agent":
            self.at(self.now, self._event, {"type": "ChannelDestroyed", "cause": 16, "channel": {"id": cid}})
        return True

    # ── what the brain says happened ──────────────────────────────────────
    def _observe(self, kind: str, data: dict[str, Any]) -> None:
        self.events.append((self.now, kind, data))
        record = self.records.get(str(data.get("call")))
        if kind == m.CALL_STARTED and record:
            record.queue = data["queue"]
        elif kind == m.CALL_OFFERED and record:
            record.rung.append(self.core.config.agents[data["agent"]].number)
        elif (
            kind == m.CALL_OFFER_FAILED
            and data.get("agent")
            and data["reason"] not in ("answered elsewhere", "overflowed")
        ):
            self.agent_records[self.core.config.agents[data["agent"]].number].refused += 1
        elif kind == m.CALL_OVERFLOWED and record:
            record.queue = data["to_queue"]
        elif kind == m.CALL_CONNECTED and record:
            if data["agent"] in self.talking:
                raise AssertionError(f"agent {data['agent']} connected to two calls at once")
            self.talking[data["agent"]] = record.id
            record.answered_at, record.agent = self.now, self.core.config.agents[data["agent"]].number
            self.agent_records[record.agent].answered += 1
            customer = self._channels.get(record.id)
            if customer:  # patience no longer matters; the conversation has its own length
                for token in customer["tokens"]:
                    self.cancel(token)
                customer["tokens"] = [
                    self.at(self.now + customer["plan"].talk, self._customer_leaves, record.id)
                ]
        elif kind == m.CALL_ENDED and record:
            record.ended_at, record.result = self.now, data["result"]
            if record.agent and record.answered_at is not None:
                self.agent_records[record.agent].talk_time += self.now - record.answered_at
            for uid, call in list(self.talking.items()):
                if call == record.id:
                    del self.talking[uid]
        elif kind == m.AGENT_STATE and data["activity"] == "wrapup":
            person = self.people[data["agent"]]
            self.at(
                self.now + person.wrapup_for,
                self._command,
                m.AGENT_WRAPUP_DONE,
                {"agent": person.uid, "code": "Sim"},
            )

    @staticmethod
    def _channel(cid: str, name: str, app: str, app_data: str) -> dict[str, Any]:
        return {
            "id": cid,
            "name": name,
            "protocol_id": f"sim-{cid}",
            "caller": {"name": f"Customer {cid}", "number": cid},
            "dialplan": {
                "context": "lab",
                "exten": "800",
                "priority": 2,
                "app_name": app,
                "app_data": app_data,
            },
        }
