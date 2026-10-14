"""GraphQL queries for files and the attachment-type catalogue."""

import logging

import graphene
from django.db.models import Q

from apps.attachments import policies, selectors
from apps.attachments.inputs import AttachmentDefinitionFilterInput, AttachmentFilterInput
from apps.attachments.models import Attachment
from apps.attachments.permissions import VIEW_ALL_ATTACHMENTS, VIEW_ATTACHMENT_SETTINGS
from apps.attachments.types import (
    AllowedFileExtensionType,
    AttachmentDefinitionType,
    AttachmentStatsType,
    AttachmentType,
)
from apps.core.decorators import authenticated, has_permission, log_errors_and_warnings
from apps.core.enums import to_values
from apps.core.graphql.context import current_user
from apps.core.graphql.pagination import Page, SearchDataDto
from apps.core.graphql.response import Response
from apps.core.graphql.search import GenericSpecificationSearch

logger = logging.getLogger(__name__)


class Query(graphene.ObjectType):
    # ── Attachment types ──────────────────────────────────────────────────
    get_attachment_definitions = Response(
        AttachmentDefinitionType,
        filtering=AttachmentDefinitionFilterInput(),
        description="The attachment-type catalogue (incl. inactive). permissions=['can_view_attachment_settings']",
    )
    get_active_attachment_definitions = Response(
        AttachmentDefinitionType,
        description="Active attachment types, for forms that ask for documents. Authenticated.",
    )
    get_attachment_stats = Response(
        AttachmentStatsType,
        description="Counters for the Attachment types dashboard. permissions=['can_view_attachment_settings']",
    )
    get_allowed_file_extensions = Response(
        AllowedFileExtensionType,
        description=(
            "The file types a definition may accept, per kind — the same set the upload endpoint "
            "enforces. permissions=['can_view_attachment_settings']"
        ),
    )

    # ── Files ─────────────────────────────────────────────────────────────
    get_my_attachments_pageable = Page(
        AttachmentType,
        search_dto=SearchDataDto(required=True),
        filtering=AttachmentFilterInput(),
        description="The files I uploaded. Authenticated.",
    )
    get_attachments_pageable = Page(
        AttachmentType,
        search_dto=SearchDataDto(required=True),
        filtering=AttachmentFilterInput(),
        description="Every stored file. permissions=['can_view_all_attachments']",
    )
    get_entity_attachments = Response(
        AttachmentType,
        entity_type=graphene.String(required=True),
        entity_uid=graphene.UUID(required=True),
        description="The files attached to one record that I am allowed to open. Authenticated.",
    )

    # ── resolvers ─────────────────────────────────────────────────────────
    @staticmethod
    def _file_filters(search_dto, filtering) -> Q:
        filters = GenericSpecificationSearch.build_search_filters(search_dto, model=Attachment)
        if filtering is None:
            return filters
        if filtering.uid is not None:
            filters &= Q(uid=filtering.uid)
        if filtering.file_type is not None:
            filters &= Q(file_type=to_values(filtering.file_type))
        if filtering.purpose:
            filters &= Q(purpose=filtering.purpose)
        if filtering.entity_type:
            filters &= Q(entity_type=filtering.entity_type)
        if filtering.entity_uid is not None:
            filters &= Q(entity_uid=filtering.entity_uid)
        if filtering.uploaded_by_uid is not None:
            filters &= Q(created_by__uid=filtering.uploaded_by_uid)
        search = (filtering.search or "").strip()
        if search:
            filters &= Q(name__icontains=search)
        return filters

    @staticmethod
    @has_permission(permissions=[VIEW_ATTACHMENT_SETTINGS])
    @log_errors_and_warnings
    def resolve_get_attachment_definitions(root, info, filtering=None):
        filters = Q()
        if filtering is not None:
            if filtering.uid is not None:
                filters &= Q(uid=filtering.uid)
            if filtering.kind is not None:
                filters &= Q(kind=filtering.kind)
            if filtering.is_active is not None:
                filters &= Q(is_active=filtering.is_active)
        data = selectors.attachment_definitions_queryset(filters).order_by("name_en")
        return Response.build_response(code=9001, info=info, data_list=list(data))

    @staticmethod
    @authenticated()
    @log_errors_and_warnings
    def resolve_get_active_attachment_definitions(root, info):
        return Response.build_response(
            code=9001, info=info, data_list=selectors.active_attachment_definitions()
        )

    @staticmethod
    @has_permission(permissions=[VIEW_ATTACHMENT_SETTINGS])
    @log_errors_and_warnings
    def resolve_get_attachment_stats(root, info):
        return Response.build_response(code=9001, info=info, data=selectors.attachment_stats())

    @staticmethod
    @has_permission(permissions=[VIEW_ATTACHMENT_SETTINGS])
    @log_errors_and_warnings
    def resolve_get_allowed_file_extensions(root, info):
        return Response.build_response(code=9001, info=info, data_list=selectors.allowed_file_extensions())

    @staticmethod
    @authenticated()
    @log_errors_and_warnings
    def resolve_get_my_attachments_pageable(root, info, search_dto, filtering=None):
        filters = Query._file_filters(search_dto, filtering) & Q(created_by=current_user(info))
        data = selectors.attachments_queryset(filters)
        return Page.build_response(code=9001, info=info, pageable=search_dto.pageable_param, data=data)

    @staticmethod
    @has_permission(permissions=[VIEW_ALL_ATTACHMENTS])
    @log_errors_and_warnings
    def resolve_get_attachments_pageable(root, info, search_dto, filtering=None):
        data = selectors.attachments_queryset(Query._file_filters(search_dto, filtering))
        return Page.build_response(code=9001, info=info, pageable=search_dto.pageable_param, data=data)

    @staticmethod
    @authenticated()
    @log_errors_and_warnings
    def resolve_get_entity_attachments(root, info, entity_type, entity_uid):
        user = current_user(info)
        files = selectors.attachments_of(entity_type=entity_type, entity_uid=entity_uid)[:200]
        return Response.build_response(
            code=9001, info=info, data_list=[file for file in files if policies.can_read(user, file)]
        )
