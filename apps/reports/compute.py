"""M9: contact-centre reports, computed from the Event table (the brain's record of every decision).

Pure: events in, figures out (no database, no clock). Definitions (docs/REPORTS.md):

* A call is **offered** to every queue it waited in (``call.queued``), once per queue visit.
  Its wait in that queue runs from entering it to being answered, leaving it, or hanging up.
* **Answered within target**: answered no later than the queue's service-level seconds after
  entering it. **Service level** = answered within target / offered (as on the supervisor page).
* **Abandoned**: the caller hung up while waiting; a **short abandon** waited under
  ``short_abandon_seconds``. **Abandon rate** = abandoned / offered (short ones included).
* **Overflowed**: left the queue unanswered for another (overflow, routing, transfer).
* **Handle time** = talk (answer to the agent's leg ending, hold included) + wrap-up (to the
  agent's wrap-up), for the agent who answered in that queue.
* **Agent time in state** from ``agent.state``: available (Ready, idle), ringing, talking,
  on hold, wrap-up, not responding, and Not Ready by reason. Logged-out time is not counted.
* **AI**: calls its agents answered; resolved (contained), handed off (by reason), failed,
  caller left. **Containment** = resolved / AI calls.

A call belongs to the report when it started in [since, until); all its events count.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from contracts import messages as m

SHORT_ABANDON_SECONDS = 5
TOTAL = "All queues"
BUSY = {"offered": "ringing", "talking": "talking", "on_hold": "on_hold", "wrapup": "wrapup"}


@dataclass(frozen=True)
class Row:
    type: str
    at: datetime
    data: dict[str, Any]


def _avg(total: float, count: int) -> float | None:
    return round(total / count, 1) if count else None


def _percent(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


@dataclass
class QueueLine:
    queue: str
    target_seconds: int | None = None
    target_percent: int | None = None
    offered: int = 0
    answered: int = 0
    answered_within: int = 0
    abandoned: int = 0
    short_abandoned: int = 0
    overflowed: int = 0
    voicemail: int = 0
    other: int = 0  # ended another way: dropped by an overflow, failed
    wait_answered: float = 0.0
    longest_wait: float = 0.0
    handled: int = 0  # answered calls whose talk is known
    talk: float = 0.0
    wrapup: float = 0.0

    @property
    def service_level_percent(self) -> float | None:
        return _percent(self.answered_within, self.offered) if self.target_seconds is not None else None

    @property
    def met_target(self) -> bool | None:
        level = self.service_level_percent
        return None if level is None or self.target_percent is None else level >= self.target_percent

    @property
    def abandon_rate_percent(self) -> float | None:
        return _percent(self.abandoned, self.offered)

    @property
    def average_speed_of_answer(self) -> float | None:
        return _avg(self.wait_answered, self.answered)

    @property
    def average_talk(self) -> float | None:
        return _avg(self.talk, self.handled)

    @property
    def average_wrapup(self) -> float | None:
        return _avg(self.wrapup, self.handled)

    @property
    def average_handle_time(self) -> float | None:
        return _avg(self.talk + self.wrapup, self.handled)

    def add(self, other: "QueueLine") -> None:
        for name in (
            "offered",
            "answered",
            "answered_within",
            "abandoned",
            "short_abandoned",
            "overflowed",
            "voicemail",
            "other",
            "wait_answered",
            "handled",
            "talk",
            "wrapup",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.longest_wait = max(self.longest_wait, other.longest_wait)


@dataclass
class AgentLine:
    agent: str
    name: str = ""
    seconds: dict[str, float] = field(default_factory=dict)
    answered: int = 0
    handled: int = 0
    talk: float = 0.0
    wrapup: float = 0.0

    @property
    def average_handle_time(self) -> float | None:
        return _avg(self.talk + self.wrapup, self.handled)


@dataclass
class AiLine:
    ai_agent: str
    name: str = ""
    calls: int = 0
    resolved: int = 0
    handed_off: int = 0
    failed: int = 0
    caller_left: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    timed: int = 0

    @property
    def containment_percent(self) -> float | None:
        return _percent(self.resolved, self.calls)

    @property
    def average_seconds(self) -> float | None:
        return _avg(self.seconds, self.timed)


@dataclass
class Report:
    since: datetime
    until: datetime
    queues: list[QueueLine]
    total: QueueLine
    agents: list[AgentLine]
    ai: list[AiLine]
    experience: list[Any] = field(default_factory=list)  # apps.reports.experience.Measure, section 6


@dataclass
class _Visit:
    """One call's stay in one queue."""

    queue: str
    entered: datetime
    answered_at: datetime | None = None
    agent: str = ""
    leg: str = ""
    talk_ended: datetime | None = None
    done: bool = False


def compute(
    call_rows: list[Row],
    state_rows: list[Row],
    *,
    since: datetime,
    until: datetime,
    targets: dict[str, tuple[int, int]],
    short_abandon_seconds: int = SHORT_ABANDON_SECONDS,
) -> Report:
    """``call_rows``: the events of calls (any order); ``state_rows``: agent.state events, including
    each agent's last one before ``since``. ``targets``: queue name -> (seconds, percent)."""
    queues: dict[str, QueueLine] = {}
    agents: dict[str, AgentLine] = {}
    ai_lines: dict[str, AiLine] = {}

    def queue_line(name: str) -> QueueLine:
        if name not in queues:
            seconds, percent = targets.get(name, (None, None))
            queues[name] = QueueLine(name, seconds, percent)
        return queues[name]

    def agent_line(uid: str) -> AgentLine:
        return agents.setdefault(uid, AgentLine(uid))

    by_call: dict[str, list[Row]] = defaultdict(list)
    for row in call_rows:
        if row.data.get("call"):
            by_call[str(row.data["call"])].append(row)
    for rows in by_call.values():
        rows.sort(key=lambda row: row.at)
        started = next((row for row in rows if row.type == m.CALL_STARTED), None)
        if started is None or not since <= started.at < until:
            continue
        _one_call(rows, started, queue_line, agent_line, ai_lines, short_abandon_seconds)

    _agent_states(state_rows, since, until, agent_line)

    total = QueueLine(TOTAL)
    for line in queues.values():
        total.add(line)
    return Report(
        since=since,
        until=until,
        queues=[queues[name] for name in sorted(queues)],
        total=total,
        agents=[agents[uid] for uid in sorted(agents)],
        ai=[ai_lines[uid] for uid in sorted(ai_lines)],
    )


def _one_call(rows, started, queue_line, agent_line, ai_lines, short_abandon_seconds) -> None:
    legacy = not any(row.type in (m.CALL_QUEUED, m.AI_STARTED) for row in rows)  # events from before M9
    visits: list[_Visit] = []
    visit: _Visit | None = None
    if legacy and started.data.get("queue"):
        visit = _Visit(str(started.data["queue"]), started.at)
        visits.append(visit)
    ai_started: dict[str, datetime] = {}
    wrapped: dict[str, datetime] = {}

    def leave(outcome: str, at: datetime) -> None:
        """The caller leaves the current queue unanswered."""
        nonlocal visit
        if visit is None or visit.done or visit.answered_at is not None:
            return
        line = queue_line(visit.queue)
        waited = (at - visit.entered).total_seconds()
        line.longest_wait = max(line.longest_wait, waited)
        if outcome == "abandoned":
            line.abandoned += 1
            line.short_abandoned += waited < short_abandon_seconds
        elif outcome == "overflowed":
            line.overflowed += 1
        elif outcome == "voicemail":
            line.voicemail += 1
        else:
            line.other += 1
        visit.done = True

    for row in rows:
        data = row.data
        if row.type == m.CALL_QUEUED:
            queue = str(data.get("queue") or "")
            if visit is not None and visit.queue == queue and visit.answered_at is None and not visit.done:
                continue  # the same stay announced again (a restarted brain adopted the call)
            leave("overflowed", row.at)
            visit = _Visit(queue, row.at)
            visits.append(visit)
        elif (
            row.type == m.CALL_CONNECTED
            and visit is not None
            and visit.answered_at is None
            and not visit.done
        ):
            visit.answered_at, visit.agent, visit.leg = (
                row.at,
                str(data.get("agent") or ""),
                str(data.get("leg") or ""),
            )
        elif row.type == m.LEG_ENDED:
            for stay in visits:
                if stay.leg and stay.leg == data.get("leg") and stay.talk_ended is None:
                    stay.talk_ended = row.at
        elif row.type == m.CALL_VOICEMAIL:
            leave("voicemail", row.at)
        elif row.type == m.CALL_ENDED:
            result = data.get("result")
            leave(
                "abandoned" if result == "abandoned" else "voicemail" if result == "voicemail" else "other",
                row.at,
            )
            for stay in visits:
                if stay.answered_at is not None and stay.talk_ended is None:
                    stay.talk_ended = row.at
        elif row.type == m.AGENT_WRAPPED_UP:
            wrapped[str(data.get("agent") or "")] = row.at
        elif row.type == m.AI_STARTED:
            uid = str(data.get("ai_agent") or "")
            ai_lines.setdefault(uid, AiLine(uid)).calls += 1
            ai_started[uid] = row.at
        elif row.type == m.AI_ENDED:
            uid = str(data.get("ai_agent") or "")
            line = ai_lines.setdefault(uid, AiLine(uid))
            outcome = data.get("outcome")
            if outcome == "resolved":
                line.resolved += 1
            elif outcome == "handoff":
                line.handed_off += 1
                reason = str(data.get("reason") or "unknown")
                line.reasons[reason] = line.reasons.get(reason, 0) + 1
            elif outcome == "failed":
                line.failed += 1
            elif outcome == "caller_left":
                line.caller_left += 1
            if uid in ai_started:
                line.seconds += (row.at - ai_started.pop(uid)).total_seconds()
                line.timed += 1

    for stay in visits:
        if stay.answered_at is None:
            continue
        line = queue_line(stay.queue)
        waited = (stay.answered_at - stay.entered).total_seconds()
        line.answered += 1
        line.wait_answered += waited
        line.longest_wait = max(line.longest_wait, waited)
        if line.target_seconds is not None and waited <= line.target_seconds:
            line.answered_within += 1
        if stay.talk_ended is None:
            continue
        talk = (stay.talk_ended - stay.answered_at).total_seconds()
        wrap_at = wrapped.get(stay.agent)
        wrap = max(0.0, (wrap_at - stay.talk_ended).total_seconds()) if wrap_at else 0.0
        line.handled, line.talk, line.wrapup = line.handled + 1, line.talk + talk, line.wrapup + wrap
        if stay.agent:
            agent = agent_line(stay.agent)
            agent.answered += 1
            agent.handled, agent.talk, agent.wrapup = (
                agent.handled + 1,
                agent.talk + talk,
                agent.wrapup + wrap,
            )
    for stay in visits:
        queue_line(stay.queue).offered += 1


def _category(data: dict[str, Any]) -> str | None:
    availability, activity = data.get("availability"), data.get("activity")
    if availability == "logged_out":
        return None
    if activity in BUSY:
        return BUSY[str(activity)]
    if activity == "not_responding":
        return "not_responding"
    if availability == "not_ready":
        return f"not_ready: {data.get('reason') or 'no reason'}"
    return "available"


def _agent_states(rows: list[Row], since: datetime, until: datetime, agent_line) -> None:
    by_agent: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        if row.data.get("agent"):
            by_agent[str(row.data["agent"])].append(row)
    for uid, changes in by_agent.items():
        changes.sort(key=lambda row: row.at)
        for this, after in zip(changes, [*changes[1:], None], strict=True):
            start = max(this.at, since)
            end = min(after.at if after else until, until)
            category = _category(this.data)
            if category is None or end <= start:
                continue
            line = agent_line(uid)
            line.seconds[category] = round(line.seconds.get(category, 0.0) + (end - start).total_seconds(), 1)
