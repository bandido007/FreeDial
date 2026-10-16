"""How well AlgoDial works for the people using it: the HEART metrics of UX_PRINCIPLES.md section 6.

Pure, like :mod:`apps.reports.compute`: the period's Event rows in, one ``Measure`` per metric out.
Every line says what it is based on, so a metric resting on three events reads as such.
"""

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from apps.reports.compute import Row
from apps.reports.tables import duration

ACTION_BUDGET_MS = 1000  # section 3: the brain confirms an action within 1 s
ACTION_LIMIT_MS = 10_000  # section 8: confirmation or failure within 10 s
SUPERVISION = "agent.forced"  # a supervisor changed the agent's state: the problem was acted on


@dataclass(frozen=True)
class Measure:
    user: str  # Agent, Supervisor, Admin, All
    goal: str
    metric: str
    value: str
    based_on: str


def _per_100(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}" if whole else "-"


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _seconds(value: float | None) -> str:
    """Short times to a tenth of a second (scripted phones answer in 0.3 s), longer ones as m:ss."""
    if value is None:
        return "-"
    return f"{value:.1f} s" if value < 60 else duration(value)


def _minutes(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _over_limit(rows: list[Row], limits: dict[str, int], until: datetime) -> tuple[list[float], int]:
    """Not Ready episodes that ran past their reason's limit: minutes from going over to a supervisor's
    action, and how many the agent ended themselves."""
    states: dict[str, list[Row]] = defaultdict(list)
    forced: dict[str, list[datetime]] = defaultdict(list)
    for row in rows:
        agent = str(row.data.get("agent") or "")
        if row.type == "agent.state":
            states[agent].append(row)
        elif row.type == SUPERVISION:
            forced[agent].append(row.at)
    acted, by_agent = [], 0
    for agent, changes in states.items():
        changes.sort(key=lambda row: row.at)
        episode: tuple[str, datetime] | None = None  # (reason, since)
        for row, following in zip(changes, [*changes[1:], None], strict=True):
            key = (row.data.get("availability"), row.data.get("reason") or "")
            same = episode is not None and key == ("not_ready", episode[0])
            if not same:
                episode = (key[1], row.at) if key[0] == "not_ready" else None
            nxt = following.data if following else None
            continues = nxt is not None and (nxt.get("availability"), nxt.get("reason") or "") == key
            if episode is None or continues:
                continue
            reason, since = episode
            limit = limits.get(reason, 0)
            ended = following.at if following else until
            over_at = since.timestamp() + limit
            if not limit or ended.timestamp() <= over_at:
                continue
            action = next(
                (moment for moment in forced[agent] if over_at <= moment.timestamp() <= ended.timestamp()),
                None,
            )
            if action is not None:
                acted.append((action.timestamp() - over_at) / 60)
            elif following is not None:
                by_agent += 1
    return acted, by_agent


def measure(
    rows: list[Row],
    *,
    until: datetime,
    since: datetime | None = None,
    not_ready_limits: dict[str, int] | None = None,
    agents_created: dict[str, datetime] | None = None,
) -> list[Measure]:
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_type[row.type].append(row.data)
    out: list[Measure] = []

    # Agent: answer offers quickly and never miss one.
    answered, missed = by_type["offer.answered"], by_type["offer.missed"]
    offers = "Answer offers quickly and never miss one"
    ring = _median([float(d.get("seconds") or 0) for d in answered])
    out.append(
        Measure(
            "Agent",
            offers,
            "Median seconds to answer",
            _seconds(ring),
            f"{len(answered)} offers answered",
        )
    )
    out.append(
        Measure(
            "Agent",
            offers,
            "Missed offers per 100",
            _per_100(len(missed), len(answered) + len(missed)),
            f"{len(missed)} of {len(answered) + len(missed)} offers",
        )
    )

    # Agent: finish calls without fighting the tool.
    wraps, changed = by_type["wrapup.done"], by_type["wrapup.code_changed"]
    finish = "Finish calls without fighting the tool"
    wrap = _median([float(d.get("seconds") or 0) for d in wraps])
    out.append(
        Measure(
            "Agent",
            finish,
            "Median wrap-up",
            _seconds(wrap),
            f"{len(wraps)} wrap-ups",
        )
    )
    out.append(
        Measure(
            "Agent",
            finish,
            "Corrections per 100 calls",
            _per_100(len(changed), len(wraps)),
            f"{len(changed)} codes changed after {len(wraps)} wrap-ups",
        )
    )

    # Agent: transfers work first time.
    started, done = len(by_type["transfer.started"]), len(by_type["transfer.completed"])
    cancelled, failed = len(by_type["transfer.cancelled"]), len(by_type["transfer.failed"])
    out.append(
        Measure(
            "Agent",
            "Transfers work first time",
            "Transfer success rate",
            f"{_per_100(done, started)}%" if started else "-",
            f"{done} of {started} transfers; {cancelled} cancelled, {failed} failed",
        )
    )

    # Agent: the tool feels easy (survey; not built).
    out.append(
        Measure(
            "Agent",
            "The tool feels easy",
            "Average ease (1 to 5)",
            "-",
            "Not collected yet: the 2-weekly survey is not built",
        )
    )

    # Agent: callers do not repeat themselves.
    handoffs = sum(1 for d in by_type["ai.ended"] if d.get("outcome") == "handoff")
    wrong = len(by_type["ux.ai_summary_wrong"])
    out.append(
        Measure(
            "Agent",
            "Callers do not repeat themselves",
            "Summary-wrong rate per 100 AI handoffs",
            _per_100(wrong, handoffs),
            f"{wrong} of {handoffs} handoffs",
        )
    )
    queue_wrong = len(by_type["ux.ai_queue_wrong"])
    out.append(
        Measure(
            "Agent",
            "Callers do not repeat themselves",
            "Wrong-queue rate per 100 AI handoffs",
            _per_100(queue_wrong, handoffs),
            f"{queue_wrong} of {handoffs} handoffs",
        )
    )

    # Supervisor: problems are noticed and acted on.
    acted, by_agent = _over_limit(rows, not_ready_limits or {}, until)
    out.append(
        Measure(
            "Supervisor",
            "Problems are noticed and acted on",
            "Median minutes to action",
            _minutes(_median(acted)),
            f"{len(acted) + by_agent} times over a limit: {len(acted)} acted on by a supervisor, "
            f"{by_agent} ended by the agent",
        )
    )

    # Admin: setup is quick and safe.
    created = {uid: when for uid, when in (agents_created or {}).items() if since is None or when >= since}
    first: dict[str, datetime] = {}
    for row in rows:
        agent = str(row.data.get("agent") or "")
        if row.type == "offer.answered" and agent in created and agent not in first:
            first[agent] = row.at
    setup = [(first[uid] - created[uid]).total_seconds() / 60 for uid in first]
    out.append(
        Measure(
            "Admin",
            "Setup is quick and safe",
            "Minutes from creating an agent to their first call",
            _minutes(_median(setup)),
            f"{len(first)} of {len(created)} new agents took a call",
        )
    )
    errors = Counter(str(d.get("model") or "?") for d in by_type["ux.admin_validation_error"])
    out.append(
        Measure(
            "Admin",
            "Setup is quick and safe",
            "Errors caught before saving",
            str(sum(errors.values())),
            ", ".join(f"{model} {count}" for model, count in errors.most_common(3)) or "none",
        )
    )

    # All: speed budgets hold (a button's round trip, per action).
    actions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in by_type["ux.action"]:
        actions[str(d.get("action") or "?")].append(d)
    for action, presses in sorted(actions.items()):
        times = [float(d.get("ms") or 0) for d in presses]
        p95 = _p95(times)
        late = sum(1 for d in presses if not d.get("ok", True) or float(d.get("ms") or 0) > ACTION_LIMIT_MS)
        verdict = "within" if p95 <= ACTION_BUDGET_MS else "over"
        out.append(
            Measure(
                "All",
                "Speed budgets hold",
                f"95th percentile: {action}",
                f"{p95 / 1000:.1f} s (budget {ACTION_BUDGET_MS // 1000} s): {verdict}",
                f"{len(presses)} presses; {late} not confirmed within {ACTION_LIMIT_MS // 1000} s",
            )
        )
    if not actions:
        out.append(
            Measure("All", "Speed budgets hold", "95th percentile per button", "-", "no presses recorded")
        )
    return out
