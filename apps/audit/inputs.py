"""GraphQL input types for the audit domain."""

import graphene

from apps.audit.enums import AuditActionEnum, RequestKindEnum


class AuditLogFilterInput(graphene.InputObjectType):
    actor_uid = graphene.UUID()
    action = graphene.Field(AuditActionEnum)
    entity = graphene.String(description='Model label, e.g. "users.User"')
    app_label = graphene.String()
    object_uid = graphene.UUID()
    request_id = graphene.String()
    search = graphene.String(description="Free text across actor and record")
    date_from = graphene.DateTime(description="created_at >= this")
    date_to = graphene.DateTime(description="created_at <= this")


class EndpointLogFilterInput(graphene.InputObjectType):
    kind = graphene.Field(RequestKindEnum)
    is_resolved = graphene.Boolean()
    user_uid = graphene.UUID()
    request_id = graphene.String()
    operation = graphene.String(description="Matches the operations column (contains)")
    error_type = graphene.String()
    date_from = graphene.DateTime(description="created_at >= this")
    date_to = graphene.DateTime(description="created_at <= this")
