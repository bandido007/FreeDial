"""GraphQL mutations for roles and permissions.

Mutations are deliberately thin: validate the envelope of arguments, delegate
to the services for the actual work, and wrap the outcome.
"""

import logging

import graphene

from apps.core.decorators import has_permission, log_errors_and_warnings
from apps.core.enums import AccountTypeChoices
from apps.core.graphql.context import username
from apps.core.graphql.response import Response
from apps.core.hooks import forget_permissions
from apps.core.utils import Utils
from apps.rbac import services
from apps.rbac.inputs import RoleInput
from apps.rbac.permissions import ASSIGN_ROLE_PERMISSIONS, CREATE_ROLE, DELETE_ROLE, EDIT_ROLE
from apps.rbac.types import RoleType

logger = logging.getLogger(__name__)

# Platform operators (System origin) may always manage roles, so a mistake in
# the role editor can never leave the system with nobody able to fix it.
ADMIN_TYPES = [AccountTypeChoices.System.value]


class CreateRole(graphene.Mutation):
    class Arguments:
        input = RoleInput(required=True)

    Output = Response.get_response_type(RoleType)

    @classmethod
    @has_permission(permissions=[CREATE_ROLE], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        if not Utils.empty_and_null_checker(input, ["name"]):
            return Response.build_response(code=9005, info=info)

        logger.info("[CreateRole] by %s", username(info))
        role = services.create_role(name=input.name, description=input.description or "")
        if input.permission_uids:
            role = services.assign_permissions_to_role(
                role_uid=role.uid, permission_uids=input.permission_uids
            )
        return Response.build_response(code=9001, info=info, data=role)


class UpdateRole(graphene.Mutation):
    class Arguments:
        input = RoleInput(required=True)

    Output = Response.get_response_type(RoleType)

    @classmethod
    @has_permission(permissions=[EDIT_ROLE], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        if not Utils.empty_and_null_checker(input, ["uid"]):
            return Response.build_response(code=9005, info=info)

        logger.info("[UpdateRole] %s by %s", input.uid, username(info))
        role = services.update_role(uid=input.uid, name=input.name, description=input.description)
        return Response.build_response(code=9001, info=info, data=role)


class DeleteRole(graphene.Mutation):
    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(RoleType)

    @classmethod
    @has_permission(permissions=[DELETE_ROLE], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        logger.info("[DeleteRole] %s by %s", uid, username(info))
        role = services.delete_role(uid=uid)
        return Response.build_response(code=9001, info=info, data=role)


class AssignPermissionsToRole(graphene.Mutation):
    class Arguments:
        input = RoleInput(required=True)

    Output = Response.get_response_type(RoleType)

    @classmethod
    @has_permission(permissions=[ASSIGN_ROLE_PERMISSIONS], user_types=ADMIN_TYPES)
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        if not Utils.empty_and_null_checker(input, ["uid"]):
            return Response.build_response(code=9005, info=info)

        logger.info("[AssignPermissionsToRole] %s by %s", input.uid, username(info))
        role = services.assign_permissions_to_role(
            role_uid=input.uid, permission_uids=input.permission_uids or []
        )
        forget_permissions(info.context)
        return Response.build_response(code=9001, info=info, data=role)


class Mutation(graphene.ObjectType):
    create_role = CreateRole.Field()
    update_role = UpdateRole.Field()
    delete_role = DeleteRole.Field()
    assign_permissions_to_role = AssignPermissionsToRole.Field()
