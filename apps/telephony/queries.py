"""GraphQL queries of the telephony app.

A resolver reads the request, builds filters, asks a selector, and wraps the
answer in the envelope. Nothing else.
"""

import graphene
from django.db.models import Q

from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.enums import to_values
from apps.core.graphql.pagination import Page, SearchDataDto
from apps.core.graphql.response import Response
from apps.core.graphql.search import GenericSpecificationSearch
from apps.telephony import selectors
from apps.telephony.inputs import SipAccountFilterInput
from apps.telephony.models import SipAccount
from apps.telephony.permissions import VIEW_SIP_ACCOUNTS
from apps.telephony.types import SipAccountType


def _filters(search_dto, filtering) -> Q:
    filters = GenericSpecificationSearch.build_search_filters(search_dto, model=SipAccount)
    if filtering is not None:
        if filtering.uid is not None:
            filters &= Q(uid=filtering.uid)
        if filtering.kind is not None:
            filters &= Q(kind=to_values(filtering.kind))
        if filtering.active is not None:
            filters &= Q(active=filtering.active)
        search = (filtering.search or "").strip()
        if search:
            filters &= Q(sip_id__icontains=search) | Q(display_name__icontains=search)
    return filters


class Query(graphene.ObjectType):
    get_sip_account = Response(
        SipAccountType,
        uid=graphene.UUID(required=True),
        description="One SIP account. permissions=['can_view_sip_accounts']",
    )
    get_sip_accounts_pageable = Page(
        SipAccountType,
        search_dto=SearchDataDto(required=True),
        filtering=SipAccountFilterInput(),
        description="Paginated, searchable SIP accounts. permissions=['can_view_sip_accounts']",
    )

    @staticmethod
    @has_permission(permissions=[VIEW_SIP_ACCOUNTS])
    @log_errors_and_warnings
    def resolve_get_sip_account(root, info, uid):
        return Response.build_response(code=9001, info=info, data=selectors.get_sip_account(uid))

    @staticmethod
    @has_permission(permissions=[VIEW_SIP_ACCOUNTS])
    @log_errors_and_warnings
    def resolve_get_sip_accounts_pageable(root, info, search_dto, filtering=None):
        rows = selectors.sip_accounts_queryset(_filters(search_dto, filtering))
        return Page.build_response(code=9001, info=info, pageable=search_dto.pageable_param, data=rows)
