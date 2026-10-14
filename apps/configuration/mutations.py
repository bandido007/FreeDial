"""GraphQL mutations for system settings."""

import logging

import graphene
from graphene.types.generic import GenericScalar

from apps.configuration import services
from apps.configuration.permissions import MANAGE_SYSTEM_SETTINGS
from apps.configuration.types import SystemSettingType
from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.enums import AccountTypeChoices
from apps.core.graphql.context import current_user, username
from apps.core.graphql.response import Response

logger = logging.getLogger(__name__)

ADMIN_TYPES = [AccountTypeChoices.System.value]


class UpdateSystemSetting(graphene.Mutation):
    """Save a new value for one setting."""

    class Arguments:
        key = graphene.String(required=True)
        value = GenericScalar(required=True)

    Output = Response.get_response_type(SystemSettingType)

    @classmethod
    @has_permission(permissions=[MANAGE_SYSTEM_SETTINGS], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, key, value):
        logger.info("[UpdateSystemSetting] %s by %s", key, username(info))
        row = services.set_value(key=key, value=value, actor=current_user(info))
        return Response.build_response(code=9001, info=info, data=row)


class ResetSystemSetting(graphene.Mutation):
    """Put one setting back to its default."""

    class Arguments:
        key = graphene.String(required=True)

    Output = Response.get_response_type(SystemSettingType)

    @classmethod
    @has_permission(permissions=[MANAGE_SYSTEM_SETTINGS], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, key):
        logger.info("[ResetSystemSetting] %s by %s", key, username(info))
        row = services.reset_value(key=key, actor=current_user(info))
        return Response.build_response(code=9001, info=info, data=row)


class Mutation(graphene.ObjectType):
    update_system_setting = UpdateSystemSetting.Field(
        description="permissions=['can_manage_system_settings']"
    )
    reset_system_setting = ResetSystemSetting.Field(description="permissions=['can_manage_system_settings']")
