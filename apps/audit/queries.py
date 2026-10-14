"""GraphQL queries for the audit domain."""

import logging

import graphene
from django.conf import settings
from django.db.models import Q

from apps.audit import selectors
from apps.audit.inputs import AuditLogFilterInput, EndpointLogFilterInput
from apps.audit.models import AuditLog, RequestLog
from apps.audit.permissions import (
    EXPORT_AUDIT_LOGS,
    MANAGE_ENDPOINT_LOGS,
    VIEW_AUDIT_LOGS,
    VIEW_ENDPOINT_LOGS,
)
from apps.audit.types import AuditLogType, AuditStatsType, ErrorReportSubscriptionType, RequestLogType
from apps.core.decorators import authenticated, has_permission, log_errors_and_warnings
from apps.core.enums import to_values
from apps.core.exceptions import ValidationError
from apps.core.exports import ExportFileType, TableExportFormatEnum, build_table, is_supported
from apps.core.graphql.context import current_user
from apps.core.graphql.pagination import Page, SearchDataDto
from apps.core.graphql.response import Response
from apps.core.graphql.search import GenericSpecificationSearch
from apps.core.ratelimit import throttle
from apps.core.validators import validate_date_range

logger = logging.getLogger(__name__)

EXPORT_ROW_LIMIT = 5_000


def _audit_log_filters(search_dto, filtering) -> Q:
    filters = GenericSpecificationSearch.build_search_filters(search_dto, model=AuditLog)
    if filtering is not None:
        if filtering.actor_uid is not None:
            filters &= Q(actor__uid=filtering.actor_uid)
        if filtering.action is not None:
            filters &= Q(action=to_values(filtering.action))
        if filtering.entity:
            filters &= Q(entity__iexact=filtering.entity)
        if filtering.app_label:
            filters &= Q(app_label=filtering.app_label)
        if filtering.object_uid is not None:
            filters &= Q(object_uid=filtering.object_uid)
        if filtering.request_id:
            filters &= Q(request_id=filtering.request_id)
        search = (filtering.search or "").strip()
        if search:
            filters &= (
                Q(actor_email__icontains=search)
                | Q(actor_name__icontains=search)
                | Q(object_repr__icontains=search)
                | Q(entity__icontains=search)
            )
        validate_date_range(filtering.date_from, filtering.date_to)
        if filtering.date_from is not None:
            filters &= Q(created_at__gte=filtering.date_from)
        if filtering.date_to is not None:
            filters &= Q(created_at__lte=filtering.date_to)
    return filters


def _endpoint_log_filters(search_dto, filtering) -> Q:
    filters = GenericSpecificationSearch.build_search_filters(search_dto, model=RequestLog)
    if filtering is not None:
        if filtering.kind is not None:
            filters &= Q(kind=to_values(filtering.kind))
        if filtering.is_resolved is not None:
            filters &= Q(is_resolved=filtering.is_resolved)
        if filtering.user_uid is not None:
            filters &= Q(user__uid=filtering.user_uid)
        if filtering.request_id:
            filters &= Q(request_id=filtering.request_id)
        if filtering.operation:
            filters &= Q(operations__icontains=filtering.operation)
        if filtering.error_type:
            filters &= Q(error_type__iexact=filtering.error_type)
        validate_date_range(filtering.date_from, filtering.date_to)
        if filtering.date_from is not None:
            filters &= Q(created_at__gte=filtering.date_from)
        if filtering.date_to is not None:
            filters &= Q(created_at__lte=filtering.date_to)
    return filters


class Query(graphene.ObjectType):
    # ── System-wide (oversight) ─────────────────────────────────────────────
    get_audit_logs_pageable = Page(
        AuditLogType,
        search_dto=SearchDataDto(required=True),
        filtering=AuditLogFilterInput(),
        description="System-wide change trail. permissions=['can_view_audit_logs']",
    )
    get_record_history = Response(
        AuditLogType,
        entity=graphene.String(required=True, description='Model label, e.g. "users.User"'),
        object_uid=graphene.UUID(required=True),
        description="Everything that happened to one record, oldest first. permissions=['can_view_audit_logs']",
    )
    get_audited_entities = graphene.Field(
        graphene.List(graphene.String),
        description="Model labels present in the trail. permissions=['can_view_audit_logs']",
    )
    export_audit_logs = Response(
        ExportFileType,
        format=TableExportFormatEnum(required=True),
        filtering=AuditLogFilterInput(),
        description=(
            "The change trail as a downloadable file, honouring the same filters. "
            "permissions=['can_export_audit_logs']"
        ),
    )
    get_endpoint_logs_pageable = Page(
        RequestLogType,
        search_dto=SearchDataDto(required=True),
        filtering=EndpointLogFilterInput(),
        description=(
            "Endpoint transaction log — ERROR level only (defects a developer must fix); "
            "INFO/WARNING rows never surface here. permissions=['can_view_endpoint_logs']"
        ),
    )
    get_audit_stats = Response(
        AuditStatsType,
        description=(
            "Counters for the audit dashboard. permissions=['can_view_audit_logs','can_view_endpoint_logs']"
        ),
    )
    get_error_report_recipients = Response(
        ErrorReportSubscriptionType,
        description="Users subscribed to the daily error report. permissions=['can_manage_endpoint_logs']",
    )

    # ── Per-user (any authenticated account, always self-scoped) ───────────
    get_my_activity_pageable = Page(
        AuditLogType,
        search_dto=SearchDataDto(required=True),
        filtering=AuditLogFilterInput(),
        description="The signed-in user's own action trail. Any authenticated user.",
    )

    # ── Resolvers ───────────────────────────────────────────────────────────
    @staticmethod
    @has_permission(permissions=[VIEW_AUDIT_LOGS])
    @log_errors_and_warnings
    def resolve_get_audit_logs_pageable(root, info, search_dto, filtering=None):
        data = selectors.audit_logs_queryset(_audit_log_filters(search_dto, filtering))
        return Page.build_response(code=9001, info=info, pageable=search_dto.pageable_param, data=data)

    @staticmethod
    @has_permission(permissions=[VIEW_AUDIT_LOGS])
    @log_errors_and_warnings
    def resolve_get_record_history(root, info, entity, object_uid):
        rows = selectors.record_history_queryset(entity=entity, object_uid=object_uid)[:500]
        return Response.build_response(code=9001, info=info, data_list=list(rows))

    @staticmethod
    @has_permission(permissions=[VIEW_AUDIT_LOGS])
    def resolve_get_audited_entities(root, info):
        return selectors.audited_entities()

    @staticmethod
    @has_permission(permissions=[EXPORT_AUDIT_LOGS])
    @log_errors_and_warnings
    def resolve_export_audit_logs(root, info, format, filtering=None):
        fmt = getattr(format, "value", format)
        if not is_supported(fmt):
            raise ValidationError("Unsupported export format", code=9002)
        throttle("export-audit", current_user(info).pk, limit=6, window=60)
        logs = selectors.audit_logs_queryset(_audit_log_filters(None, filtering))[:EXPORT_ROW_LIMIT]
        rows = [
            (
                log.created_at,
                log.actor_name or log.actor_email or "System",
                log.action,
                log.entity,
                log.object_repr,
                ", ".join(sorted(log.changes or {})),
                log.ip_address,
            )
            for log in logs
        ]
        export = build_table(
            title="Activity trail",
            subtitle=settings.PROJECT_NAME,
            columns=["When", "Who", "Action", "Record type", "Record", "Fields changed", "Address"],
            rows=rows,
            fmt=fmt,
        )
        return Response.build_response(code=9001, info=info, data=export.as_payload())

    @staticmethod
    @has_permission(permissions=[VIEW_ENDPOINT_LOGS])
    @log_errors_and_warnings
    def resolve_get_endpoint_logs_pageable(root, info, search_dto, filtering=None):
        # level=ERROR is enforced in the selector — not filterable away.
        data = selectors.endpoint_errors_queryset(_endpoint_log_filters(search_dto, filtering))
        return Page.build_response(code=9001, info=info, pageable=search_dto.pageable_param, data=data)

    @staticmethod
    @has_permission(permissions=[VIEW_AUDIT_LOGS, VIEW_ENDPOINT_LOGS])
    @log_errors_and_warnings
    def resolve_get_audit_stats(root, info):
        return Response.build_response(code=9001, info=info, data=selectors.audit_stats())

    @staticmethod
    @has_permission(permissions=[MANAGE_ENDPOINT_LOGS])
    @log_errors_and_warnings
    def resolve_get_error_report_recipients(root, info):
        return Response.build_response(
            code=9001, info=info, data_list=list(selectors.error_report_recipients())
        )

    @staticmethod
    @authenticated()
    @log_errors_and_warnings
    def resolve_get_my_activity_pageable(root, info, search_dto, filtering=None):
        user = current_user(info)
        data = selectors.my_activity_queryset(user, _audit_log_filters(search_dto, filtering))
        return Page.build_response(code=9001, info=info, pageable=search_dto.pageable_param, data=data)
