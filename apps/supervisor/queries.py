"""GraphQL queries of the supervisor page."""

import graphene

from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.graphql.context import current_user
from apps.core.graphql.response import Response
from apps.supervisor import desk
from apps.supervisor.permissions import SUPERVISE
from apps.supervisor.types import AgentHistoryType, SupervisorDeskType, TeamMeasuresType


class Query(graphene.ObjectType):
    supervised_agent_history = Response(
        AgentHistoryType,
        agent=graphene.String(required=True),
        description="Recent events of an agent in your teams. permissions=['can_monitor_calls', 'can_whisper_calls', 'can_barge_calls', 'can_force_agent_state']",
    )

    @staticmethod
    @has_permission(permissions=list(SUPERVISE))
    @log_errors_and_warnings
    def resolve_supervised_agent_history(root, info, agent):
        return Response.build_response(
            code=9001, info=info, data_list=desk.agent_history(current_user(info), agent)
        )

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

    my_team_measures = Response(
        TeamMeasuresType,
        period=graphene.String(description="today | yesterday | last_7_days"),
        team=graphene.String(description="One of your teams (uid); empty: all of them"),
        description="How your teams are doing (UX_PRINCIPLES.md section 6): answers, missed offers, wrap-up, "
        "transfers, AI summaries flagged wrong, minutes to act on long breaks; per agent too. "
        "permissions=['can_monitor_calls', 'can_whisper_calls', 'can_barge_calls', 'can_force_agent_state']",
    )

    @staticmethod
    @has_permission(permissions=list(SUPERVISE))
    @log_errors_and_warnings
    def resolve_my_team_measures(root, info, period="today", team=None):
        return Response.build_response(
            code=9001,
            info=info,
            data=desk.team_measures(current_user(info), period=period, team=team or None),
        )
