"""GraphQL mutations for the audit domain."""

import logging

import graphene

from apps.audit import selectors, services
from apps.audit.permissions import MANAGE_ENDPOINT_LOGS
from apps.audit.types import (
    ErrorReportOutcomeType,
    ErrorReportSubscriptionType,
    RequestLogType,
    ResolvedCountType,
)
from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.graphql.context import current_user, username
from apps.core.graphql.response import Response
from apps.core.ratelimit import throttle

logger = logging.getLogger(__name__)


class ResolveEndpointError(graphene.Mutation):
    """Mark an endpoint error as fixed."""

    class Arguments:
        uid = graphene.UUID(required=True)
        note = graphene.String()

    Output = Response.get_response_type(RequestLogType)

    @classmethod
    @has_permission(permissions=[MANAGE_ENDPOINT_LOGS])
    @log_errors_and_warnings
    def mutate(cls, root, info, uid, note=""):
        logger.info("[ResolveEndpointError] %s by %s", uid, username(info))
        log = services.resolve_endpoint_error(uid=uid, note=note or "", resolver=current_user(info))
        return Response.build_response(code=9001, info=info, data=log)


class ResolveSimilarEndpointErrors(graphene.Mutation):
    """Resolve every open occurrence of the same defect in one action."""

    class Arguments:
        uid = graphene.UUID(required=True)
        note = graphene.String()

    Output = Response.get_response_type(ResolvedCountType)

    @classmethod
    @has_permission(permissions=[MANAGE_ENDPOINT_LOGS])
    @log_errors_and_warnings
    def mutate(cls, root, info, uid, note=""):
        count = services.resolve_similar_errors(uid=uid, note=note or "", resolver=current_user(info))
        return Response.build_response(
            code=9001,
            info=info,
            data={"resolved": count},
            custom_message=f"{count} error(s) marked as resolved.",
        )


class ReopenEndpointError(graphene.Mutation):
    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(RequestLogType)

    @classmethod
    @has_permission(permissions=[MANAGE_ENDPOINT_LOGS])
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        log = services.reopen_endpoint_error(uid=uid)
        return Response.build_response(code=9001, info=info, data=log)


class SetErrorReportRecipients(graphene.Mutation):
    """Choose who receives the daily error report (replace-set)."""

    class Arguments:
        user_uids = graphene.List(graphene.UUID, required=True)

    Output = Response.get_response_type(ErrorReportSubscriptionType)

    @classmethod
    @has_permission(permissions=[MANAGE_ENDPOINT_LOGS])
    @log_errors_and_warnings
    def mutate(cls, root, info, user_uids):
        services.set_error_report_recipients(user_uids=user_uids or [])
        return Response.build_response(
            code=9001, info=info, data_list=list(selectors.error_report_recipients())
        )


class SendErrorReportNow(graphene.Mutation):
    """Send the error report now, covering the last 24 hours."""

    Output = Response.get_response_type(ErrorReportOutcomeType)

    @classmethod
    @has_permission(permissions=[MANAGE_ENDPOINT_LOGS])
    @log_errors_and_warnings
    def mutate(cls, root, info):
        from datetime import timedelta

        from django.utils import timezone

        throttle("error-report-now", current_user(info).pk, limit=3, window=600)
        now = timezone.localtime()
        outcome = services.send_error_report(since=now - timedelta(days=1), until=now)
        message = (
            f"Report sent to {outcome['recipients']} recipient(s)."
            if outcome["sent"]
            else "Nothing was sent: there are no errors to report, or no recipients are chosen."
        )
        return Response.build_response(code=9001, info=info, data=outcome, custom_message=message)


class Mutation(graphene.ObjectType):
    resolve_endpoint_error = ResolveEndpointError.Field(
        description="permissions=['can_manage_endpoint_logs']"
    )
    resolve_similar_endpoint_errors = ResolveSimilarEndpointErrors.Field(
        description="permissions=['can_manage_endpoint_logs']"
    )
    reopen_endpoint_error = ReopenEndpointError.Field(description="permissions=['can_manage_endpoint_logs']")
    set_error_report_recipients = SetErrorReportRecipients.Field(
        description="permissions=['can_manage_endpoint_logs']"
    )
    send_error_report_now = SendErrorReportNow.Field(description="permissions=['can_manage_endpoint_logs']")
