"""GraphQL mutations of the telephony app.

A mutation checks that the arguments are there, calls one service, and wraps
the outcome. Rules live in ``services.py``.
"""

import graphene

from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.enums import to_values
from apps.core.graphql.context import current_user
from apps.core.graphql.response import Response
from apps.core.utils import Utils
from apps.telephony import services
from apps.telephony.inputs import SipAccountInput
from apps.telephony.permissions import MANAGE_SIP_ACCOUNTS
from apps.telephony.types import SipAccountType


class SaveSipAccount(graphene.Mutation):
    """Create (no uid) or update (uid) a SIP account. Asterisk sees the change at once."""

    class Arguments:
        input = SipAccountInput(required=True)

    Output = Response.get_response_type(SipAccountType)

    @classmethod
    @has_permission(permissions=[MANAGE_SIP_ACCOUNTS])
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        actor = current_user(info)
        kind = to_values(input.kind) if input.kind is not None else None
        if input.uid:
            account = services.update_sip_account(
                uid=input.uid,
                actor=actor,
                sip_id=input.sip_id,
                kind=kind,
                password=input.password,
                display_name=input.display_name,
                codecs=input.codecs,
                context=input.context,
            )
            return Response.build_response(code=9001, info=info, data=account)
        if not Utils.empty_and_null_checker(input, ["sip_id", "kind"]):
            return Response.build_response(code=9005, info=info)
        account = services.create_sip_account(
            actor=actor,
            sip_id=input.sip_id,
            kind=kind,
            password=input.password,
            display_name=input.display_name or "",
            codecs=input.codecs,
            context=input.context,
        )
        return Response.build_response(code=9001, info=info, data=account)


class SetSipAccountActive(graphene.Mutation):
    """Switch a SIP account on or off. Off removes it from Asterisk; its phones can no longer register."""

    class Arguments:
        uid = graphene.UUID(required=True)
        active = graphene.Boolean(required=True)

    Output = Response.get_response_type(SipAccountType)

    @classmethod
    @has_permission(permissions=[MANAGE_SIP_ACCOUNTS])
    @log_errors_and_warnings
    def mutate(cls, root, info, uid, active):
        account = services.set_sip_account_active(uid=uid, actor=current_user(info), active=active)
        return Response.build_response(code=9001, info=info, data=account)


class DeleteSipAccount(graphene.Mutation):
    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(SipAccountType)

    @classmethod
    @has_permission(permissions=[MANAGE_SIP_ACCOUNTS])
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        return Response.build_response(code=9001, info=info, data=services.delete_sip_account(uid=uid))


class Mutation(graphene.ObjectType):
    save_sip_account = SaveSipAccount.Field(
        description="Create or update a SIP account. permissions=['can_manage_sip_accounts']"
    )
    set_sip_account_active = SetSipAccountActive.Field(
        description="Switch a SIP account on or off. permissions=['can_manage_sip_accounts']"
    )
    delete_sip_account = DeleteSipAccount.Field(
        description="Remove a SIP account. permissions=['can_manage_sip_accounts']"
    )
