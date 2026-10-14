"""GraphQL queries of the agents app."""

import graphene

from apps.agentdesk import desk
from apps.agentdesk.types import AgentDeskType
from apps.agents.permissions import TAKE_CALLS
from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.graphql.context import current_user
from apps.core.graphql.response import Response


class Query(graphene.ObjectType):
    my_agent_desk = Response(
        AgentDeskType,
        description="The signed-in agent, their live state, Not Ready reasons and wrap-up codes. "
        "permissions=['can_take_calls']",
    )

    @staticmethod
    @has_permission(permissions=[TAKE_CALLS])
    @log_errors_and_warnings
    def resolve_my_agent_desk(root, info):
        return Response.build_response(code=9001, info=info, data=desk.desk(current_user(info)))
