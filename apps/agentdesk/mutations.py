"""GraphQL mutations of the agents app: the agent page's buttons.

Each asks the brain with a command and returns its ``commandId``; what the brain
did comes back on the live WebSocket.
"""

import graphene
from graphene.types.generic import GenericScalar

from apps.agentdesk import desk
from apps.agentdesk.types import AgentPhoneType, CommandSentType
from apps.agents.permissions import TAKE_CALLS
from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.graphql.context import current_user
from apps.core.graphql.response import Response
from contracts import messages as m


class StartAgentSession(graphene.Mutation):
    """Sign the agent in on their phone: the desk phone set in admin, or else the browser phone with a
    fresh SIP password for this session. The brain is told where to ring them."""

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
        notes = graphene.String(description="Call notes, up to 8,000 characters; recorded with wrap-up")
        ready = graphene.Boolean(
            description="Next availability, chosen atomically before another call can be offered"
        )
        reason = graphene.String(description="Not Ready reason when ready is false")

    Output = Response.get_response_type(CommandSentType)

    @classmethod
    @has_permission(permissions=[TAKE_CALLS])
    @log_errors_and_warnings
    def mutate(cls, root, info, code, notes=None, ready=None, reason=""):
        data = desk.finish_wrapup(current_user(info), code=code, notes=notes, ready=ready, reason=reason)
        return Response.build_response(code=9001, info=info, data=data)


class ChangeMyWrapupCode(graphene.Mutation):
    class Arguments:
        call = graphene.String(required=True, description="The call just wrapped up")
        code = graphene.String(required=True, description="One of myAgentDesk.wrapupCodes")

    Output = Response.get_response_type(CommandSentType)

    @classmethod
    @has_permission(permissions=[TAKE_CALLS])
    @log_errors_and_warnings
    def mutate(cls, root, info, call, code):
        data = desk.change_wrapup_code(current_user(info), call=call, code=code)
        return Response.build_response(code=9001, info=info, data=data)


class RecordUxSignal(graphene.Mutation):
    class Arguments:
        signal = graphene.String(required=True, description="One of the ux.* page signals")
        signal_id = graphene.String(
            required=True, description="A UUID the page makes; a retry is stored once"
        )
        call = graphene.String()
        data = GenericScalar()

    Output = Response.get_response_type(CommandSentType)

    @classmethod
    @has_permission(permissions=[TAKE_CALLS])
    @log_errors_and_warnings
    def mutate(cls, root, info, signal, signal_id, call=None, data=None):
        result = desk.record_ux_signal(
            current_user(info), signal=signal, signal_id=signal_id, call=call, data=data
        )
        return Response.build_response(code=9001, info=info, data=result)


class SkillNeedInput(graphene.InputObjectType):
    skill = graphene.String(required=True)
    min_level = graphene.Int(required=True)


class TransferMyCall(graphene.Mutation):
    """Hand the current call on: blind or consult, to an agent (uid), a queue (name) or a number."""

    class Arguments:
        kind = graphene.String(required=True, description="blind or consult")
        target_kind = graphene.String(required=True, description="agent, queue or number")
        target = graphene.String(required=True)
        skills = graphene.List(SkillNeedInput, description="Blind to a queue: extra required skills")

    Output = Response.get_response_type(CommandSentType)

    @classmethod
    @has_permission(permissions=[TAKE_CALLS])
    @log_errors_and_warnings
    def mutate(cls, root, info, kind, target_kind, target, skills=None):
        levels = {need.skill: need.min_level for need in skills or []}
        data = desk.transfer(
            current_user(info), kind=kind, target_kind=target_kind, target=target, skills=levels
        )
        return Response.build_response(code=9001, info=info, data=data)


class TransferStep(graphene.Mutation):
    """complete / cancel a consult, conference (all three together), drop_other / leave a conference."""

    class Arguments:
        step = graphene.String(required=True, description="complete, cancel, conference, drop_other or leave")

    Output = Response.get_response_type(CommandSentType)

    STEPS = {
        "complete": (m.TRANSFER_COMPLETE, None),
        "cancel": (m.TRANSFER_CANCEL, None),
        "conference": (m.CONFERENCE_START, None),
        "drop_other": (m.CONFERENCE_DROP, "other"),
        "leave": (m.CONFERENCE_DROP, "me"),
    }

    @classmethod
    @has_permission(permissions=[TAKE_CALLS])
    @log_errors_and_warnings
    def mutate(cls, root, info, step):
        if step not in cls.STEPS:
            return Response.build_response(code=10206, info=info)
        command, who = cls.STEPS[step]
        return Response.build_response(
            code=9001, info=info, data=desk.transfer_step(current_user(info), command, who)
        )


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
    transfer_my_call = TransferMyCall.Field(
        description="Transfer the current call. permissions=['can_take_calls']"
    )
    my_transfer_step = TransferStep.Field(
        description="Complete, cancel, conference, drop the other party or leave. permissions=['can_take_calls']"
    )
    finish_my_wrapup = FinishMyWrapup.Field(
        description="End wrap-up with a code. permissions=['can_take_calls']"
    )
    change_my_wrapup_code = ChangeMyWrapupCode.Field(
        description="Correct the last call's wrap-up code, within 2 minutes. permissions=['can_take_calls']"
    )
    record_ux_signal = RecordUxSignal.Field(
        description="A UX measurement from the agent page (UX_PRINCIPLES.md 6). permissions=['can_take_calls']"
    )
