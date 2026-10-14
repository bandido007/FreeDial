"""GraphQL queries for the users domain.

Resolvers stay thin: they read context, build filters, and delegate to
selectors, then wrap the result in the standard envelope. All read logic lives
in ``apps/users/selectors.py``.
"""

import logging

import graphene
from django.conf import settings

from apps.core.decorators import authenticated, has_permission, log_errors_and_warnings
from apps.core.exceptions import ValidationError
from apps.core.exports import ExportFileType, TableExportFormatEnum, build_table, is_supported
from apps.core.graphql.context import current_user, username
from apps.core.graphql.pagination import Page, SearchDataDto
from apps.core.graphql.response import Response
from apps.core.graphql.search import GenericSpecificationSearch
from apps.core.ratelimit import throttle
from apps.users import data_export, registration_services, selectors, services
from apps.users.inputs import UserFilterInput
from apps.users.models import User
from apps.users.permissions import EXPORT_USERS, VIEW_USERS
from apps.users.types import MyDataExportType, RegistrationStatusType, UserConsentType, UserType

logger = logging.getLogger(__name__)

EXPORT_ROW_LIMIT = 10_000


class Query(graphene.ObjectType):
    get_all_users_pageable = Page(
        UserType,
        search_dto=SearchDataDto(required=True),
        filtering=UserFilterInput(),
        description="Paginated, filterable directory of users. permissions=['can_view_users']",
    )
    get_user = Response(
        UserType,
        uid=graphene.UUID(required=True),
        description="A single user with their roles. permissions=['can_view_users']",
    )
    export_users = Response(
        ExportFileType,
        format=TableExportFormatEnum(required=True),
        filtering=UserFilterInput(),
        description=(
            "The user listing as a downloadable file (PDF / EXCEL / CSV / WORD), honouring "
            "the same filters as the pageable query. permissions=['can_export_users']"
        ),
    )
    get_user_consents = Response(
        UserConsentType,
        user_uid=graphene.UUID(required=True),
        description="A user's legal-consent history (audit). permissions=['can_view_users']",
    )
    export_my_data = Response(
        MyDataExportType, description="A portable snapshot of the caller's personal data."
    )
    get_registration_status = Response(
        RegistrationStatusType,
        description="Whether people may create their own account, and the policy versions. Public.",
    )

    # ── resolvers ─────────────────────────────────────────────────────────
    @staticmethod
    def _listing(info, search_dto, filtering):
        viewer = current_user(info)
        # Soft-deleted accounts are hidden unless an authorised caller asks for
        # them explicitly (deleted=true reads through the all_objects manager).
        include_deleted = bool(filtering and filtering.deleted)
        filters = GenericSpecificationSearch.build_search_filters(search_dto, model=User)
        filters &= selectors.build_user_filters(filtering)
        scope = selectors.visibility_scope_q(viewer)
        if scope is not None:
            filters &= scope
        return selectors.users_queryset(filters, include_deleted=include_deleted)

    @staticmethod
    @has_permission(permissions=[VIEW_USERS])
    @log_errors_and_warnings
    def resolve_get_all_users_pageable(root, info, search_dto, filtering=None):
        data = Query._listing(info, search_dto, filtering)
        logger.info("[getAllUsersPageable] by %s", username(info))
        return Page.build_response(code=9001, info=info, pageable=search_dto.pageable_param, data=data)

    @staticmethod
    @has_permission(permissions=[VIEW_USERS])
    @log_errors_and_warnings
    def resolve_get_user(root, info, uid):
        user = selectors.get_user(uid, include_deleted=True)
        # Enforce the same visibility scope as the listing on the detail view.
        if not selectors.is_visible_to(current_user(info), user):
            return Response.build_response(code=9000, info=info)
        return Response.build_response(code=9001, info=info, data=user)

    @staticmethod
    @has_permission(permissions=[EXPORT_USERS])
    @log_errors_and_warnings
    def resolve_export_users(root, info, format, filtering=None):
        fmt = getattr(format, "value", format)
        if not is_supported(fmt):
            raise ValidationError("Unsupported export format", code=9002)
        # A full export is heavy and holds personal data: a few per minute.
        throttle("export-users", current_user(info).pk, limit=6, window=60)
        users = Query._listing(info, None, filtering).order_by("first_name", "last_name")[:EXPORT_ROW_LIMIT]
        rows = [
            (
                user.get_full_name(),
                user.email,
                user.phone,
                user.account_type,
                ", ".join(sorted(grant.role.name for grant in user.user_roles.all())),
                user.account_status,
                user.is_verified,
                user.mfa_enabled,
                user.last_login,
                user.date_joined,
            )
            for user in users
        ]
        export = build_table(
            title="Users",
            subtitle=settings.PROJECT_NAME,
            columns=[
                "Name",
                "E-mail",
                "Phone",
                "Account type",
                "Roles",
                "Status",
                "Verified",
                "Two-factor",
                "Last sign-in",
                "Joined",
            ],
            rows=rows,
            fmt=fmt,
        )
        logger.info("[exportUsers] %s rows as %s by %s", len(rows), fmt, username(info))
        return Response.build_response(code=9001, info=info, data=export.as_payload())

    @staticmethod
    @has_permission(permissions=[VIEW_USERS])
    @log_errors_and_warnings
    def resolve_get_user_consents(root, info, user_uid):
        target = selectors.get_user(user_uid, include_deleted=True)
        if not selectors.is_visible_to(current_user(info), target):
            return Response.build_response(code=9000, info=info)
        return Response.build_response(code=9001, info=info, data_list=list(target.consents.all()))

    @staticmethod
    @authenticated()
    @log_errors_and_warnings
    def resolve_export_my_data(root, info):
        payload = services.export_my_data(user=current_user(info))
        return Response.build_response(
            code=9001,
            info=info,
            data={"data": payload, "sections": data_export.registered_sections()},
        )

    @staticmethod
    @log_errors_and_warnings
    def resolve_get_registration_status(root, info):
        return Response.build_response(
            code=9001,
            info=info,
            data={
                "self_registration_open": registration_services.self_registration_open(),
                "terms_version": settings.CONSENT_TERMS_VERSION,
                "privacy_version": settings.CONSENT_PRIVACY_VERSION,
            },
        )
