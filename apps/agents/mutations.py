"""GraphQL mutations of the agents app: the agent page's buttons.

Each asks the brain with a command and returns its ``commandId``; what the brain
did comes back on the live WebSocket.
"""

import graphene

from apps.agents import desk
from apps.agents.permissions import TAKE_CALLS
from apps.agents.types import AgentPhoneType, CommandSentType
from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.graphql.context import current_user
from apps.core.graphql.response import Response


class StartAgentSession(graphene.Mutation):
    """Sign the agent's browser phone in: a fresh SIP password for this session, and the brain told."""

    Output = Response.get_response_type(AgentPhoneType)

    @classmethod
    @has_permission(permissions=[TAKE_CALLS])
    @log_errors_and_warnings
    def mutate(cls, root, info):
        return Response.build_response(code=9001, info=info, data=desk.start_session(current_user(info)))


class EndAgentSession(graphene.Mutation):
    Output = Response.get_response_type(CommandSentType)

    @classmethod
    @has_permission(permissions=[TAKE_CALLS])
    @log_errors_and_warnings
    def mutate(cls, root, info):
        return Response.build_response(code=9001, info=info, data=desk.end_session(current_user(info)))


class SetMyAvailability(graphene.Mutation):
    class Arguments:
        ready = graphene.Boolean(required=True)
        reason = graphene.String(description="One of myAgentDesk.notReadyReasons when ready is false")

    Output = Response.get_response_type(CommandSentType)

    @classmethod
    @has_permission(permissions=[TAKE_CALLS])
    @log_errors_and_warnings
    def mutate(cls, root, info, ready, reason=""):
        data = desk.set_availability(current_user(info), ready=ready, reason=reason or "")
        return Response.build_response(code=9001, info=info, data=data)


class HoldMyCall(graphene.Mutation):
    class Arguments:
        hold = graphene.Boolean(required=True, description="true: hold (customer hears music); false: resume")

    Output = Response.get_response_type(CommandSentType)

    @classmethod
    @has_permission(permissions=[TAKE_CALLS])
    @log_errors_and_warnings
    def mutate(cls, root, info, hold):
        return Response.build_response(code=9001, info=info, data=desk.hold(current_user(info), on=hold))


class FinishMyWrapup(graphene.Mutation):
    class Arguments:
        code = graphene.String(required=True, description="One of myAgentDesk.wrapupCodes")

    Output = Response.get_response_type(CommandSentType)

    @classmethod
    @has_permission(permissions=[TAKE_CALLS])
    @log_errors_and_warnings
    def mutate(cls, root, info, code):
        data = desk.finish_wrapup(current_user(info), code=code)
        return Response.build_response(code=9001, info=info, data=data)


class Mutation(graphene.ObjectType):
    start_agent_session = StartAgentSession.Field(
        description="Start the browser phone for this session. permissions=['can_take_calls']"
    )
    end_agent_session = EndAgentSession.Field(
        description="Log out and switch the browser phone off. permissions=['can_take_calls']"
    )
    set_my_availability = SetMyAvailability.Field(
        description="Ready, or Not Ready with a reason. permissions=['can_take_calls']"
    )
    hold_my_call = HoldMyCall.Field(
        description="Hold or resume the current call. permissions=['can_take_calls']"
    )
    finish_my_wrapup = FinishMyWrapup.Field(
        description="End wrap-up with a code. permissions=['can_take_calls']"
    )
