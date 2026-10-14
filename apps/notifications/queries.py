"""GraphQL queries for the notifications domain.

Resolvers stay thin: build filters from the search DTO + typed filter input,
delegate to selectors, and wrap in the standard envelope. The overview is gated
by ``can_view_notifications``; the "my notifications" feeds need only a
signed-in user; the configuration reads are gated by
``can_manage_notification_config``.
"""

import logging

import graphene
from django.db.models import Q

from apps.core.decorators import authenticated, has_permission, log_errors_and_warnings
from apps.core.enums import to_values
from apps.core.graphql.context import current_user
from apps.core.graphql.pagination import Page, SearchDataDto
from apps.core.graphql.response import Response
from apps.core.graphql.search import GenericSpecificationSearch
from apps.core.validators import validate_date_range
from apps.notifications import catalogue, selectors
from apps.notifications.backends import sms as sms_backend
from apps.notifications.enums import NotificationChannelChoices
from apps.notifications.inputs import NotificationFilterInput
from apps.notifications.models import Notification
from apps.notifications.permissions import MANAGE_NOTIFICATION_CONFIG, VIEW_NOTIFICATIONS
from apps.notifications.types import (
    EmailConfigurationType,
    NotificationCategoryType,
    NotificationPreferenceType,
    NotificationTemplateType,
    NotificationType,
    SmsConfigurationType,
    SmsProviderType,
    UnreadCountType,
)

logger = logging.getLogger(__name__)


class Query(graphene.ObjectType):
    # ── Overview (admin) ──────────────────────────────────────────────────────
    get_notifications_pageable = Page(
        NotificationType,
        search_dto=SearchDataDto(required=True),
        filtering=NotificationFilterInput(),
        description="Paginated notification log (all channels/statuses). "
        "permissions=['can_view_notifications']",
    )

    # ── My feeds (authenticated) ──────────────────────────────────────────────
    get_my_notifications = Response(
        NotificationType, description="All notifications addressed to me. Authenticated."
    )
    get_my_in_app_notifications = Response(
        NotificationType, description="My in-app notifications. Authenticated."
    )
    get_my_in_app_notifications_pageable = Page(
        NotificationType,
        search_dto=SearchDataDto(required=True),
        unread_only=graphene.Boolean(default_value=False),
        description="My in-app notifications, page by page. Authenticated.",
    )
    get_my_unread_notification_count = Response(
        UnreadCountType, description="How many in-app notifications I have not read. Authenticated."
    )
    get_my_notification_preferences = Response(
        NotificationPreferenceType,
        description="Every category on every channel, with my current choice. Authenticated.",
    )

    # ── Configuration (admin) ─────────────────────────────────────────────────
    get_email_configurations = Response(
        EmailConfigurationType,
        description="SMTP configurations. permissions=['can_manage_notification_config']",
    )
    get_sms_configurations = Response(
        SmsConfigurationType,
        description="SMS gateway configurations. permissions=['can_manage_notification_config']",
    )
    get_sms_providers = Response(
        SmsProviderType,
        description="Gateway implementations available. permissions=['can_manage_notification_config']",
    )
    get_notification_templates = Response(
        NotificationTemplateType,
        description="Notification content templates. permissions=['can_manage_notification_config']",
    )
    get_notification_categories = Response(
        NotificationCategoryType,
        description="The kinds of message the system can send. Authenticated.",
    )

    # ── resolvers ─────────────────────────────────────────────────────────────
    @staticmethod
    @has_permission(permissions=[VIEW_NOTIFICATIONS])
    @log_errors_and_warnings
    def resolve_get_notifications_pageable(root, info, search_dto, filtering=None):
        filters = GenericSpecificationSearch.build_search_filters(search_dto, model=Notification)
        if filtering is not None:
            if filtering.uid is not None:
                filters &= Q(uid=filtering.uid)
            if filtering.channel is not None:
                filters &= Q(channel=to_values(filtering.channel))
            if filtering.status is not None:
                filters &= Q(status=to_values(filtering.status))
            if filtering.category:
                filters &= Q(category=filtering.category)
            if filtering.is_sent is not None:
                filters &= Q(is_sent=filtering.is_sent)
            if filtering.receiver_uid is not None:
                filters &= Q(receiver__uid=filtering.receiver_uid)
            search = (filtering.search or "").strip()
            if search:
                filters &= (
                    Q(email_to__icontains=search)
                    | Q(phone_to__icontains=search)
                    | Q(full_name__icontains=search)
                    | Q(subject__icontains=search)
                    | Q(title__icontains=search)
                )
            validate_date_range(filtering.date_from, filtering.date_to)
            if filtering.date_from is not None:
                filters &= Q(created_at__gte=filtering.date_from)
            if filtering.date_to is not None:
                filters &= Q(created_at__lte=filtering.date_to)
        data = selectors.notifications_queryset(filters)
        return Page.build_response(code=9001, info=info, pageable=search_dto.pageable_param, data=data)

    @staticmethod
    @authenticated()
    @log_errors_and_warnings
    def resolve_get_my_notifications(root, info):
        user = current_user(info)
        return Response.build_response(
            code=9001, info=info, data_list=list(selectors.my_notifications(user)[:200])
        )

    @staticmethod
    @authenticated()
    @log_errors_and_warnings
    def resolve_get_my_in_app_notifications(root, info):
        user = current_user(info)
        data = selectors.my_notifications(user, channel=NotificationChannelChoices.INAPP.value)
        return Response.build_response(code=9001, info=info, data_list=list(data[:200]))

    @staticmethod
    @authenticated()
    @log_errors_and_warnings
    def resolve_get_my_in_app_notifications_pageable(root, info, search_dto, unread_only=False):
        user = current_user(info)
        data = selectors.my_notifications(user, channel=NotificationChannelChoices.INAPP.value)
        if unread_only:
            data = data.filter(is_read=False)
        return Page.build_response(code=9001, info=info, pageable=search_dto.pageable_param, data=data)

    @staticmethod
    @authenticated()
    @log_errors_and_warnings
    def resolve_get_my_unread_notification_count(root, info):
        unread = selectors.unread_in_app_count(current_user(info))
        return Response.build_response(code=9001, info=info, data={"unread": unread})

    @staticmethod
    @authenticated()
    @log_errors_and_warnings
    def resolve_get_my_notification_preferences(root, info):
        rows = selectors.preference_matrix(current_user(info))
        return Response.build_response(code=9001, info=info, data_list=rows)

    @staticmethod
    @has_permission(permissions=[MANAGE_NOTIFICATION_CONFIG])
    @log_errors_and_warnings
    def resolve_get_email_configurations(root, info):
        return Response.build_response(
            code=9001, info=info, data_list=list(selectors.email_configs_queryset())
        )

    @staticmethod
    @has_permission(permissions=[MANAGE_NOTIFICATION_CONFIG])
    @log_errors_and_warnings
    def resolve_get_sms_configurations(root, info):
        return Response.build_response(code=9001, info=info, data_list=list(selectors.sms_configs_queryset()))

    @staticmethod
    @has_permission(permissions=[MANAGE_NOTIFICATION_CONFIG])
    @log_errors_and_warnings
    def resolve_get_sms_providers(root, info):
        providers = [
            {
                "key": key,
                "name": sms_backend.PROVIDER_LABELS.get(key, key),
                "required_settings": list(sms_backend.REQUIRED_FIELDS.get(key, ())),
            }
            for key in sorted(sms_backend.PROVIDERS)
        ]
        return Response.build_response(code=9001, info=info, data_list=providers)

    @staticmethod
    @has_permission(permissions=[MANAGE_NOTIFICATION_CONFIG])
    @log_errors_and_warnings
    def resolve_get_notification_templates(root, info):
        return Response.build_response(code=9001, info=info, data_list=list(selectors.templates_queryset()))

    @staticmethod
    @authenticated()
    @log_errors_and_warnings
    def resolve_get_notification_categories(root, info):
        rows = sorted(catalogue.categories(), key=lambda category: category.name)
        return Response.build_response(code=9001, info=info, data_list=rows)
