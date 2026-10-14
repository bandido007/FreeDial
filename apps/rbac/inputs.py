"""GraphQL input types for roles and permissions."""

import graphene


class RoleInput(graphene.InputObjectType):
    uid = graphene.UUID(description="Required when updating")
    name = graphene.String()
    description = graphene.String()
    permission_uids = graphene.List(graphene.UUID, description="Permissions to grant to this role")


class RoleFilterInput(graphene.InputObjectType):
    uid = graphene.UUID()
    is_system = graphene.Boolean()
    active = graphene.Boolean()
    search = graphene.String(description="Free text across name and description")
