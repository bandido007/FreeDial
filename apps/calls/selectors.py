"""Reads of the calls app: what happened, for reports."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Max, Q

from apps.calls.models import Call, CallResult


@dataclass(frozen=True)
class QueueStats:
    queue: str
    offered: int
    answered: int
    abandoned: int
    overflowed: int
    answered_within_target: int
    target_seconds: int
    service_level_percent: float | None  # None: no call yet
    average_wait_seconds: float | None
    longest_wait_seconds: float | None
    average_talk_seconds: float | None


def _seconds(value: timedelta | None) -> float | None:
    return None if value is None else round(value.total_seconds(), 1)


def queue_stats(*, queue: str, since: datetime, until: datetime, target_seconds: int) -> QueueStats:
    """Finished calls that ended in ``queue`` and started in [since, until).

    Service level = calls answered within ``target_seconds`` of arriving, out of every call
    offered (answered, abandoned, overflowed or failed). Waits count from the first ring of
    the customer's call, including time spent in a queue it overflowed out of.
    """
    calls = Call.objects.filter(
        queue=queue, started_at__gte=since, started_at__lt=until, ended_at__isnull=False
    )
    wait = ExpressionWrapper(F("answered_at") - F("started_at"), output_field=DurationField())
    talk = ExpressionWrapper(F("ended_at") - F("answered_at"), output_field=DurationField())
    within = Q(answered_at__lte=F("started_at") + timedelta(seconds=target_seconds))
    counts = calls.aggregate(
        offered=Count("pk"),
        answered=Count("pk", filter=Q(result=CallResult.ANSWERED)),
        abandoned=Count("pk", filter=Q(result=CallResult.ABANDONED)),
        overflowed=Count("pk", filter=Q(result=CallResult.OVERFLOW)),
        within=Count("pk", filter=Q(result=CallResult.ANSWERED) & within),
    )
    times = calls.filter(result=CallResult.ANSWERED, answered_at__isnull=False).aggregate(
        average_wait=Avg(wait), longest_wait=Max(wait), average_talk=Avg(talk)
    )
    offered = counts["offered"]
    return QueueStats(
        queue=queue,
        offered=offered,
        answered=counts["answered"],
        abandoned=counts["abandoned"],
        overflowed=counts["overflowed"],
        answered_within_target=counts["within"],
        target_seconds=target_seconds,
        service_level_percent=round(100 * counts["within"] / offered, 1) if offered else None,
        average_wait_seconds=_seconds(times["average_wait"]),
        longest_wait_seconds=_seconds(times["longest_wait"]),
        average_talk_seconds=_seconds(times["average_talk"]),
    )
