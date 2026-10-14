"""GraphQL queries for roles and permissions."""

import graphene
from django.db.models import Q

from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.graphql.pagination import Page, SearchDataDto
from apps.core.graphql.response import Response
from apps.core.graphql.search import GenericSpecificationSearch
from apps.rbac import selectors
from apps.rbac.inputs import RoleFilterInput
from apps.rbac.models import Role
from apps.rbac.permissions import VIEW_ROLES
from apps.rbac.types import PermissionGroupType, RoleType


class Query(graphene.ObjectType):
    get_all_roles_pageable = Page(
        RoleType,
        search_dto=SearchDataDto(required=True),
        filtering=RoleFilterInput(),
        description="Paginated, searchable list of roles. permissions=['can_view_roles']",
    )
    get_role = Response(
        RoleType,
        uid=graphene.UUID(required=True),
        description="One role with its permissions. permissions=['can_view_roles']",
    )
    get_all_permission_groups = Response(
        PermissionGroupType,
        description="All permission groups with their permissions. permissions=['can_view_roles']",
    )

    @staticmethod
    @has_permission(permissions=[VIEW_ROLES])
    @log_errors_and_warnings
    def resolve_get_all_roles_pageable(root, info, search_dto, filtering=None):
        filters = GenericSpecificationSearch.build_search_filters(search_dto, model=Role)
        if filtering is not None:
            if filtering.uid is not None:
                filters &= Q(uid=filtering.uid)
            if filtering.is_system is not None:
                filters &= Q(is_system=filtering.is_system)
            if filtering.active is not None:
                filters &= Q(active=filtering.active)
            search = (filtering.search or "").strip()
            if search:
                filters &= Q(name__icontains=search) | Q(description__icontains=search)
        data = selectors.roles_queryset(filters)
        return Page.build_response(code=9001, info=info, pageable=search_dto.pageable_param, data=data)

    @staticmethod
    @has_permission(permissions=[VIEW_ROLES])
    @log_errors_and_warnings
    def resolve_get_role(root, info, uid):
        return Response.build_response(code=9001, info=info, data=selectors.get_role(uid))

    @staticmethod
    @has_permission(permissions=[VIEW_ROLES])
    @log_errors_and_warnings
    def resolve_get_all_permission_groups(root, info):
        data = selectors.permission_groups_queryset()
        return Response.build_response(code=9001, info=info, data_list=list(data))
