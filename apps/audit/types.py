"""GraphQL output types for the audit domain."""

import graphene
from graphene.types.generic import GenericScalar
from graphene_django import DjangoObjectType

from apps.audit.enums import AuditActionEnum, LogLevelEnum, RequestKindEnum
from apps.audit.models import AuditLog, ErrorReportSubscription, RequestLog


class AuditLogType(DjangoObjectType):
    action = AuditActionEnum()
    actor_uid = graphene.UUID()
    # GenericScalar (not JSONString) so the client receives the diff as a real
    # object — no JSON.parse on the client.
    changes = GenericScalar()

    class Meta:
        model = AuditLog
        fields = (
            "uid",
            "actor_email",
            "actor_name",
            "action",
            "entity",
            "app_label",
            "model_name",
            "object_uid",
            "object_pk",
            "object_repr",
            "changes",
            "request_id",
            "ip_address",
            "user_agent",
            "created_at",
        )

    def resolve_actor_uid(self, info):
        return self.actor.uid if self.actor_id else None


class RequestLogType(DjangoObjectType):
    kind = RequestKindEnum()
    level = LogLevelEnum()
    user_uid = graphene.UUID()
    resolved_by_name = graphene.String()
    resolved_by_email = graphene.String()
    errors = GenericScalar()
    variables = GenericScalar()

    class Meta:
        model = RequestLog
        fields = (
            "uid",
            "request_id",
            "kind",
            "operations",
            "operation_name",
            "method",
            "path",
            "user_email",
            "status_code",
            "level",
            "duration_ms",
            "error_type",
            "error_message",
            "traceback",
            "errors",
            "variables",
            "ip_address",
            "user_agent",
            "is_resolved",
            "resolved_at",
            "resolution_note",
            "created_at",
        )

    def resolve_user_uid(self, info):
        return self.user.uid if self.user_id else None

    def resolve_resolved_by_name(self, info):
        return self.resolved_by.get_full_name() if self.resolved_by_id else ""

    def resolve_resolved_by_email(self, info):
        return self.resolved_by.email if self.resolved_by_id else ""


class ErrorReportSubscriptionType(DjangoObjectType):
    user_uid = graphene.UUID()
    user_name = graphene.String()
    user_email = graphene.String()

    class Meta:
        model = ErrorReportSubscription
        fields = ("uid", "created_at")

    def resolve_user_uid(self, info):
        return self.user.uid if self.user_id else None

    def resolve_user_name(self, info):
        return self.user.get_full_name() if self.user_id else ""

    def resolve_user_email(self, info):
        return self.user.email if self.user_id else ""


class AuditStatsType(graphene.ObjectType):
    """Counter block for the audit dashboard."""

    actions_today = graphene.Int()
    actions_total = graphene.Int()
    actors_today = graphene.Int()
    logins_today = graphene.Int()
    failed_logins_today = graphene.Int()
    errors_today = graphene.Int()
    unresolved_errors = graphene.Int()


class ResolvedCountType(graphene.ObjectType):
    resolved = graphene.Int()


class ErrorReportOutcomeType(graphene.ObjectType):
    errors = graphene.Int()
    unresolved = graphene.Int()
    recipients = graphene.Int()
    sent = graphene.Boolean()
