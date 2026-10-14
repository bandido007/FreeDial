"""The report as titled tables: what the admin page shows and what the downloads contain (one source)."""

from dataclasses import dataclass

from apps.reports.compute import QueueLine, Report

SECTIONS = ("queues", "agents", "ai")
REASONS = {
    "asked_for_person": "asked for a person",
    "ai_decided": "the AI decided",
    "failed_turns": "not understood",
    "sensitive_topic": "sensitive topic",
}
STATES = ("available", "ringing", "talking", "on_hold", "wrapup", "not_responding")


@dataclass(frozen=True)
class Table:
    title: str
    columns: list[str]
    rows: list[list[str]]


def duration(seconds: float | None) -> str:
    """12.3 -> 0:12, 75 -> 1:15, 3725 -> 1:02:05; None -> -."""
    if seconds is None:
        return "-"
    whole = int(round(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"


def _queue_row(line: QueueLine) -> list[str]:
    target = (
        f"{line.target_percent}% in {line.target_seconds} s"
        if line.target_seconds is not None and line.target_percent is not None
        else "-"
    )
    level = percent(line.service_level_percent)
    if line.met_target is not None:
        level += " (met)" if line.met_target else " (missed)"
    return [
        line.queue,
        str(line.offered),
        str(line.answered),
        level,
        target,
        f"{line.abandoned} ({line.short_abandoned} short)",
        percent(line.abandon_rate_percent),
        str(line.overflowed),
        str(line.voicemail),
        duration(line.average_speed_of_answer),
        duration(line.longest_wait if line.offered else None),
        duration(line.average_talk),
        duration(line.average_wrapup),
        duration(line.average_handle_time),
    ]


def tables(report: Report) -> dict[str, Table]:
    queues = Table(
        "Calls per queue",
        [
            "Queue",
            "Offered",
            "Answered",
            "Service level",
            "Target",
            "Abandoned",
            "Abandon rate",
            "Overflowed",
            "Voicemail",
            "Avg speed of answer",
            "Longest wait",
            "Avg talk",
            "Avg wrap-up",
            "Avg handle time",
        ],
        [_queue_row(line) for line in report.queues] + ([_queue_row(report.total)] if report.queues else []),
    )
    agent_rows = []
    for agent in report.agents:
        not_ready = {k.split(": ", 1)[1]: v for k, v in agent.seconds.items() if k.startswith("not_ready")}
        agent_rows.append(
            [
                agent.name or agent.agent,
                str(agent.answered),
                duration(agent.average_handle_time),
                *(duration(agent.seconds.get(state, 0.0)) for state in STATES),
                duration(sum(not_ready.values())),
                ", ".join(f"{reason} {duration(seconds)}" for reason, seconds in sorted(not_ready.items()))
                or "-",
            ]
        )
    agents = Table(
        "Agent time in state",
        [
            "Agent",
            "Answered",
            "Avg handle time",
            "Available",
            "Ringing",
            "Talking",
            "On hold",
            "Wrap-up",
            "Not responding",
            "Not Ready",
            "Not Ready reasons",
        ],
        agent_rows,
    )
    ai = Table(
        "AI agents: containment and handoffs",
        [
            "AI agent",
            "Calls",
            "Resolved by the AI",
            "Containment",
            "Handed to a person",
            "Handoff reasons",
            "AI failed",
            "Caller hung up",
            "Avg time with the AI",
        ],
        [
            [
                line.name or line.ai_agent,
                str(line.calls),
                str(line.resolved),
                percent(line.containment_percent),
                str(line.handed_off),
                ", ".join(
                    f"{REASONS.get(reason, reason)} {count}" for reason, count in sorted(line.reasons.items())
                )
                or "-",
                str(line.failed),
                str(line.caller_left),
                duration(line.average_seconds),
            ]
            for line in report.ai
        ],
    )
    return {"queues": queues, "agents": agents, "ai": ai}
