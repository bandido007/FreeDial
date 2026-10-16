"""The report for a period, read from the Event table (see :mod:`apps.reports.compute`)."""

from datetime import date, datetime, time, timedelta

from django.db.models import Subquery
from django.utils import timezone

from apps.agents.models import Agent, NotReadyReason
from apps.ai.models import AiAgent
from apps.calls.models import Event
from apps.queues.models import Queue
from apps.reports.compute import Report, Row, compute
from apps.reports.experience import measure
from contracts import messages as m

PRESETS = {
    "today": "Today",
    "yesterday": "Yesterday",
    "last_7_days": "Last 7 days",
    "last_30_days": "Last 30 days",
    "custom": "Dates",
}


def period(
    preset: str = "today", start: date | None = None, end: date | None = None
) -> tuple[datetime, datetime]:
    """[since, until) in local time. ``custom``: from ``start`` to the end of ``end`` (both days included)."""
    today = timezone.localdate()

    def midnight(day: date) -> datetime:
        return timezone.make_aware(datetime.combine(day, time.min))

    match preset:
        case "yesterday":
            return midnight(today - timedelta(days=1)), midnight(today)
        case "last_7_days":
            return midnight(today - timedelta(days=6)), midnight(today + timedelta(days=1))
        case "last_30_days":
            return midnight(today - timedelta(days=29)), midnight(today + timedelta(days=1))
        case "custom" if start and end and start <= end:
            return midnight(start), midnight(end + timedelta(days=1))
        case "today":
            return midnight(today), midnight(today + timedelta(days=1))
    raise ValueError("unknown or invalid period")


def _rows(events) -> list[Row]:
    return [Row(event.type, event.at, event.data) for event in events.only("type", "at", "data")]


def build_report(*, since: datetime, until: datetime) -> Report:
    until = min(until, timezone.now())  # the future has no events, and agents' states run to now
    started = Event.objects.filter(type=m.CALL_STARTED, at__gte=since, at__lt=until).values("call_id")
    calls = (
        Event.objects.filter(call_id__in=Subquery(started)).exclude(type=m.AGENT_STATE).order_by("at", "id")
    )
    states = Event.objects.filter(type=m.AGENT_STATE, at__gte=since, at__lt=until).order_by("at", "id")
    before = (  # each agent's state when the period began
        Event.objects.filter(type=m.AGENT_STATE, at__lt=since)
        .order_by("agent_uid", "-at", "-id")
        .distinct("agent_uid")
    )
    targets = {
        queue.name: (queue.service_level_seconds, queue.service_level_percent)
        for queue in Queue.objects.filter(deleted=False)
    }
    report = compute(_rows(calls), _rows(before) + _rows(states), since=since, until=until, targets=targets)
    agents = {str(agent.uid): f"{agent.number} {agent.name}" for agent in Agent.objects.all()}
    for line in report.agents:
        line.name = agents.get(line.agent, line.agent)
    ai_names = {str(ai.uid): ai.name for ai in AiAgent.objects.all()}
    for ai in report.ai:
        ai.name = ai_names.get(ai.ai_agent, ai.ai_agent or "unknown")
    signals = Event.objects.filter(type__in=EXPERIENCE_SIGNALS, at__gte=since, at__lt=until).order_by(
        "at", "id"
    )
    report.experience = measure(
        _rows(before) + _rows(states) + _rows(signals),
        since=since,
        until=until,
        not_ready_limits=dict(NotReadyReason.objects.values_list("name", "max_seconds")),
        agents_created={str(agent.uid): agent.created_at for agent in Agent.objects.all()},
    )
    return report


# What the section 6 metrics read (besides the agents' states, read above).
EXPERIENCE_SIGNALS = (
    m.OFFER_ANSWERED,
    m.OFFER_MISSED,
    m.WRAPUP_DONE,
    m.WRAPUP_CODE_CHANGED,
    m.TRANSFER_STARTED,
    m.TRANSFER_COMPLETED,
    m.TRANSFER_CANCELLED,
    m.TRANSFER_FAILED,
    m.AI_ENDED,
    m.UX_AI_SUMMARY_WRONG,
    m.UX_AI_QUEUE_WRONG,
    m.AGENT_FORCED,
    m.UX_ACTION,
    m.UX_ADMIN_VALIDATION_ERROR,
)
