"""GraphQL mutations of the supervisor page: each one a command to the brain."""

import graphene

from apps.agentdesk.types import AgentPhoneType, CommandSentType
from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.graphql.context import current_user
from apps.core.graphql.response import Response
from apps.supervisor import desk
from apps.supervisor.permissions import (
    BARGE_CALLS,
    FORCE_AGENT_STATE,
    MONITOR_CALLS,
    SUPERVISE,
    WHISPER_CALLS,
)


class StartSupervisorSession(graphene.Mutation):
    """The supervisor's browser phone for this session (a fresh password, switched off at the end)."""

    Output = Response.get_response_type(AgentPhoneType)

    @classmethod
    @has_permission(permissions=list(SUPERVISE))
    @log_errors_and_warnings
    def mutate(cls, root, info):
        return Response.build_response(code=9001, info=info, data=desk.start_session(current_user(info)))


class EndSupervisorSession(graphene.Mutation):
    Output = Response.get_response_type(CommandSentType)

    @classmethod
    @has_permission(permissions=list(SUPERVISE))
    @log_errors_and_warnings
    def mutate(cls, root, info):
        return Response.build_response(code=9001, info=info, data=desk.end_session(current_user(info)))


class SuperviseCall(graphene.Mutation):
    """Monitor, whisper or barge (or switch to it). Your phone rings once and stays on as you switch."""

    class Arguments:
        call = graphene.String(required=True)
        mode = graphene.String(required=True, description="monitor, whisper or barge")

    Output = Response.get_response_type(CommandSentType)

    @classmethod
    @has_permission(permissions=[MONITOR_CALLS, WHISPER_CALLS, BARGE_CALLS])
    @log_errors_and_warnings
    def mutate(cls, root, info, call, mode):
        return Response.build_response(
            code=9001, info=info, data=desk.supervise(current_user(info), call=call, mode=mode)
        )


class StopSupervising(graphene.Mutation):
    Output = Response.get_response_type(CommandSentType)

    @classmethod
    @has_permission(permissions=list(SUPERVISE))
    @log_errors_and_warnings
    def mutate(cls, root, info):
        return Response.build_response(code=9001, info=info, data=desk.stop(current_user(info)))


class ForceAgentState(graphene.Mutation):
    class Arguments:
        agent = graphene.String(required=True)
        state = graphene.String(required=True, description="ready, not_ready or logged_out")
        reason = graphene.String(description="For not_ready")

    Output = Response.get_response_type(CommandSentType)

    @classmethod
    @has_permission(permissions=[FORCE_AGENT_STATE])
    @log_errors_and_warnings
    def mutate(cls, root, info, agent, state, reason=""):
        data = desk.force_agent_state(current_user(info), agent=agent, state=state, reason=reason or "")
        return Response.build_response(code=9001, info=info, data=data)


class Mutation(graphene.ObjectType):
    start_supervisor_session = StartSupervisorSession.Field(description="The supervisor's browser phone.")
    end_supervisor_session = EndSupervisorSession.Field(
        description="Stop supervising and switch the phone off."
    )
    supervise_call = SuperviseCall.Field(description="Monitor, whisper or barge into a call of your teams.")
    stop_supervising = StopSupervising.Field(description="Hang up the supervision.")
    force_agent_state = ForceAgentState.Field(
        description="Ready, Not Ready or log out an agent of your teams."
    )
