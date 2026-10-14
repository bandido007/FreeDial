"""GraphQL queries of the queues app."""

from dataclasses import asdict
from datetime import datetime, time

import graphene
from django.utils import timezone

from apps.calls import selectors as calls
from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.graphql.response import Response
from apps.queues import services
from apps.queues.permissions import VIEW_QUEUES
from apps.queues.types import QueueStatsType


def queue_stats(queue, since: datetime | None = None, until: datetime | None = None) -> dict:
    """The queue's stats from ``since`` (default: today's midnight) to ``until`` (default: now)."""
    now = timezone.now()
    since = since or timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    until = until or now
    stats = calls.queue_stats(
        queue=queue.name, since=since, until=until, target_seconds=queue.service_level_seconds
    )
    level = stats.service_level_percent
    return {
        **asdict(stats),
        "since": since,
        "until": until,
        "target_percent": queue.service_level_percent,
        "met_target": None if level is None else level >= queue.service_level_percent,
    }


class Query(graphene.ObjectType):
    get_queue_stats = Response(
        QueueStatsType,
        uid=graphene.UUID(required=True),
        since=graphene.DateTime(description="Default: today at midnight"),
        until=graphene.DateTime(description="Default: now"),
        description="Calls offered, answered, abandoned and overflowed, waits and service level. "
        "permissions=['can_view_queues']",
    )

    @staticmethod
    @has_permission(permissions=[VIEW_QUEUES])
    @log_errors_and_warnings
    def resolve_get_queue_stats(root, info, uid, since=None, until=None):
        data = queue_stats(services.get_queue(uid), since, until)
        return Response.build_response(code=9001, info=info, data=data)
