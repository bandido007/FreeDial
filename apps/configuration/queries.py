"""GraphQL queries for system settings."""

import graphene
from django.conf import settings

from apps.configuration import services
from apps.configuration.permissions import MANAGE_SYSTEM_SETTINGS, VIEW_SYSTEM_SETTINGS
from apps.configuration.types import PublicSettingsType, SystemSettingType
from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.graphql.response import Response


class Query(graphene.ObjectType):
    get_system_settings = Response(
        SystemSettingType,
        group=graphene.String(description="Only the settings of this group"),
        description=(
            "Every runtime setting with its value. "
            "permissions=['can_view_system_settings','can_manage_system_settings']"
        ),
    )
    get_public_settings = Response(
        PublicSettingsType,
        description="The settings the sign-in page needs (name, logo, support contact). Public.",
    )

    @staticmethod
    @has_permission(permissions=[VIEW_SYSTEM_SETTINGS, MANAGE_SYSTEM_SETTINGS])
    @log_errors_and_warnings
    def resolve_get_system_settings(root, info, group=None):
        rows = services.all_settings()
        if group:
            rows = [row for row in rows if row["group"].lower() == group.lower()]
        return Response.build_response(code=9001, info=info, data_list=rows)

    @staticmethod
    @log_errors_and_warnings
    def resolve_get_public_settings(root, info):
        values = services.public_values()
        return Response.build_response(
            code=9001,
            info=info,
            data={"values": values, "project_name": values.get("SYSTEM_NAME") or settings.PROJECT_NAME},
        )
