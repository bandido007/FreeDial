"""GraphQL queries for the authentication domain."""

import logging

import graphene
from django.db.models import Q

from apps.audit.permissions import VIEW_AUDIT_LOGS
from apps.authentication import external_services, selectors, session_services
from apps.authentication.inputs import (
    AuthenticationEventFilterInput,
    LdapConfigFilterInput,
    LoginHistoryFilterInput,
)
from apps.authentication.models import AuthenticationAudit, UserLoginHistory
from apps.authentication.permissions import VIEW_LDAP_CONFIG, VIEW_LOGIN_HISTORY
from apps.authentication.types import (
    AuthenticationEventType,
    LdapConfigType,
    LoggedInUserType,
    LoginHistoryType,
    SessionType,
    SignInProviderType,
)
from apps.core.decorators import authenticated, has_permission, log_errors_and_warnings
from apps.core.enums import to_values
from apps.core.graphql.context import current_user, username
from apps.core.graphql.pagination import Page, SearchDataDto
from apps.core.graphql.response import Response
from apps.core.graphql.search import GenericSpecificationSearch
from apps.core.validators import validate_date_range

logger = logging.getLogger(__name__)


def _login_history_filters(search_dto, filtering) -> Q:
    filters = GenericSpecificationSearch.build_search_filters(search_dto, model=UserLoginHistory)
    if filtering is not None:
        if filtering.user_uid is not None:
            filters &= Q(user__uid=filtering.user_uid)
        if filtering.is_new_device is not None:
            filters &= Q(is_new_device=filtering.is_new_device)
        search = (filtering.search or "").strip()
        if search:
            filters &= (
                Q(user__email__icontains=search)
                | Q(ip_address__icontains=search)
                | Q(browser__icontains=search)
                | Q(os__icontains=search)
            )
        validate_date_range(filtering.date_from, filtering.date_to)
        if filtering.date_from is not None:
            filters &= Q(logged_in_at__gte=filtering.date_from)
        if filtering.date_to is not None:
            filters &= Q(logged_in_at__lte=filtering.date_to)
    return filters


def _event_filters(search_dto, filtering) -> Q:
    filters = GenericSpecificationSearch.build_search_filters(search_dto, model=AuthenticationAudit)
    if filtering is not None:
        if filtering.user_uid is not None:
            filters &= Q(user__uid=filtering.user_uid)
        if filtering.event_type is not None:
            filters &= Q(event_type=to_values(filtering.event_type))
        if filtering.status:
            filters &= Q(status=filtering.status.lower())
        if filtering.request_id:
            filters &= Q(request_id=filtering.request_id)
        search = (filtering.search or "").strip()
        if search:
            filters &= Q(email__icontains=search) | Q(ip_address__icontains=search)
        validate_date_range(filtering.date_from, filtering.date_to)
        if filtering.date_from is not None:
            filters &= Q(created_at__gte=filtering.date_from)
        if filtering.date_to is not None:
            filters &= Q(created_at__lte=filtering.date_to)
    return filters


class Query(graphene.ObjectType):
    # The "who am I": identity, what I may do, and what I must do first.
    me_query = Response(
        LoggedInUserType,
        description="Current user: identity, account type, authorities, roles and any restriction.",
    )
    get_sign_in_providers = Response(
        SignInProviderType, description="External providers the sign-in page can offer. Public."
    )

    # ── Self-service ────────────────────────────────────────────────────────
    get_my_sessions = Response(
        SessionType, description="The current user's live sign-in sessions (access tokens)."
    )
    get_my_login_history_pageable = Page(
        LoginHistoryType,
        search_dto=SearchDataDto(required=True),
        filtering=LoginHistoryFilterInput(),
        description="The signed-in user's own sign-in history. Any authenticated user.",
    )
    get_my_security_events_pageable = Page(
        AuthenticationEventType,
        search_dto=SearchDataDto(required=True),
        filtering=AuthenticationEventFilterInput(),
        description="The signed-in user's own security events. Any authenticated user.",
    )

    # ── Oversight ───────────────────────────────────────────────────────────
    get_login_history_pageable = Page(
        LoginHistoryType,
        search_dto=SearchDataDto(required=True),
        filtering=LoginHistoryFilterInput(),
        description=(
            "System-wide sign-in history. permissions=['can_view_login_history','can_view_audit_logs']"
        ),
    )
    get_authentication_events_pageable = Page(
        AuthenticationEventType,
        search_dto=SearchDataDto(required=True),
        filtering=AuthenticationEventFilterInput(),
        description=(
            "Every security event: sign-ins, failures, password and two-factor changes. "
            "permissions=['can_view_login_history','can_view_audit_logs']"
        ),
    )
    get_ldap_configs = Response(
        LdapConfigType,
        filtering=LdapConfigFilterInput(),
        description="Directory (LDAP) configurations. permissions=['can_view_ldap_config']",
    )

    # ── resolvers ─────────────────────────────────────────────────────────
    @staticmethod
    @authenticated()
    @log_errors_and_warnings
    def resolve_me_query(root, info):
        user = current_user(info)
        logger.info("[meQuery] %s", username(info))
        payload = selectors.get_me_payload(user, info.context)
        return Response.build_response(code=9001, info=info, data=payload)

    @staticmethod
    @log_errors_and_warnings
    def resolve_get_sign_in_providers(root, info):
        offered = list(external_services.providers().values())
        return Response.build_response(code=9001, info=info, data_list=offered)

    @staticmethod
    @authenticated()
    @log_errors_and_warnings
    def resolve_get_my_sessions(root, info):
        sessions = session_services.list_sessions(user=current_user(info), request=info.context)
        return Response.build_response(code=9001, info=info, data_list=sessions)

    @staticmethod
    @authenticated()
    @log_errors_and_warnings
    def resolve_get_my_login_history_pageable(root, info, search_dto, filtering=None):
        data = selectors.my_login_history_queryset(
            current_user(info), _login_history_filters(search_dto, filtering)
        )
        return Page.build_response(code=9001, info=info, pageable=search_dto.pageable_param, data=data)

    @staticmethod
    @authenticated()
    @log_errors_and_warnings
    def resolve_get_my_security_events_pageable(root, info, search_dto, filtering=None):
        data = selectors.my_authentication_events_queryset(
            current_user(info), _event_filters(search_dto, filtering)
        )
        return Page.build_response(code=9001, info=info, pageable=search_dto.pageable_param, data=data)

    @staticmethod
    @has_permission(permissions=[VIEW_LOGIN_HISTORY, VIEW_AUDIT_LOGS])
    @log_errors_and_warnings
    def resolve_get_login_history_pageable(root, info, search_dto, filtering=None):
        data = selectors.login_history_queryset(_login_history_filters(search_dto, filtering))
        return Page.build_response(code=9001, info=info, pageable=search_dto.pageable_param, data=data)

    @staticmethod
    @has_permission(permissions=[VIEW_LOGIN_HISTORY, VIEW_AUDIT_LOGS])
    @log_errors_and_warnings
    def resolve_get_authentication_events_pageable(root, info, search_dto, filtering=None):
        data = selectors.authentication_events_queryset(_event_filters(search_dto, filtering))
        return Page.build_response(code=9001, info=info, pageable=search_dto.pageable_param, data=data)

    @staticmethod
    @has_permission(permissions=[VIEW_LDAP_CONFIG])
    @log_errors_and_warnings
    def resolve_get_ldap_configs(root, info, filtering=None):
        filters = None
        if filtering is not None:
            filters = Q()
            if filtering.uid is not None:
                filters &= Q(uid=filtering.uid)
            if filtering.is_active is not None:
                filters &= Q(is_active=filtering.is_active)
            if filtering.active is not None:
                filters &= Q(active=filtering.active)
        data = selectors.ldap_configs_queryset(filters)
        return Response.build_response(code=9001, info=info, data_list=list(data))
