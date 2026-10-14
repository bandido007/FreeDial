"""The report for a period, read from the Event table (see :mod:`apps.reports.compute`)."""

from datetime import date, datetime, time, timedelta

from django.db.models import Subquery
from django.utils import timezone

from apps.agents.models import Agent
from apps.ai.models import AiAgent
from apps.calls.models import Event
from apps.queues.models import Queue
from apps.reports.compute import Report, Row, compute
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
    return report
