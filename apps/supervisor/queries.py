"""GraphQL queries of the supervisor page."""

import graphene

from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.graphql.context import current_user
from apps.core.graphql.response import Response
from apps.supervisor import desk
from apps.supervisor.permissions import SUPERVISE
from apps.supervisor.types import SupervisorDeskType


class Query(graphene.ObjectType):
    my_supervisor_desk = Response(
        SupervisorDeskType,
        description="Your teams now: agents, live calls, queues. "
        "permissions=['can_monitor_calls', 'can_whisper_calls', 'can_barge_calls', 'can_force_agent_state']",
    )

    @staticmethod
    @has_permission(permissions=list(SUPERVISE))
    @log_errors_and_warnings
    def resolve_my_supervisor_desk(root, info):
        return Response.build_response(code=9001, info=info, data=desk.desk(current_user(info)))
