"""GraphQL queries of the teams app.

A resolver reads the request, builds filters, asks a selector, and wraps the
answer in the envelope. Nothing else.
"""

import graphene
from django.db.models import Q

from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.graphql.pagination import Page, SearchDataDto
from apps.core.graphql.response import Response
from apps.core.graphql.search import GenericSpecificationSearch
from apps.teams import selectors
from apps.teams.inputs import TeamFilterInput
from apps.teams.models import Team
from apps.teams.permissions import VIEW_TEAMS
from apps.teams.types import TeamType


def _filters(search_dto, filtering) -> Q:
    filters = GenericSpecificationSearch.build_search_filters(search_dto, model=Team)
    if filtering is not None:
        if filtering.uid is not None:
            filters &= Q(uid=filtering.uid)
        search = (filtering.search or "").strip()
        if search:
            filters &= Q(name__icontains=search) | Q(description__icontains=search)
    return filters


class Query(graphene.ObjectType):
    get_team = Response(
        TeamType,
        uid=graphene.UUID(required=True),
        description="One team with its members. permissions=['can_view_teams']",
    )
    get_teams_pageable = Page(
        TeamType,
        search_dto=SearchDataDto(required=True),
        filtering=TeamFilterInput(),
        description="Paginated, searchable teams. permissions=['can_view_teams']",
    )

    @staticmethod
    @has_permission(permissions=[VIEW_TEAMS])
    @log_errors_and_warnings
    def resolve_get_team(root, info, uid):
        return Response.build_response(code=9001, info=info, data=selectors.get_team(uid))

    @staticmethod
    @has_permission(permissions=[VIEW_TEAMS])
    @log_errors_and_warnings
    def resolve_get_teams_pageable(root, info, search_dto, filtering=None):
        rows = selectors.teams_queryset(_filters(search_dto, filtering))
        return Page.build_response(code=9001, info=info, pageable=search_dto.pageable_param, data=rows)
