"""GraphQL output types for roles and permissions."""

import graphene
from graphene_django import DjangoObjectType

from apps.rbac.models import Permission, PermissionGroup, Role


class PermissionType(DjangoObjectType):
    """A capability as the role editor shows it: short name, plain-language
    description and a weight (VIEW / CHANGE / DECIDE)."""

    access_level = graphene.String(
        description="VIEW = look only · CHANGE = create/edit/act · DECIDE = approve/publish/delete/"
        "grant access (high impact)."
    )

    class Meta:
        model = Permission
        fields = ("uid", "code", "name", "description", "group", "created_at")

    def resolve_access_level(self, info):
        return self.level


class PermissionGroupType(DjangoObjectType):
    permissions = graphene.List(PermissionType)

    class Meta:
        model = PermissionGroup
        fields = ("uid", "name", "description", "created_at")

    def resolve_permissions(self, info):
        return self.permissions.all()


class RoleType(DjangoObjectType):
    permissions = graphene.List(PermissionType)
    permission_count = graphene.Int()
    user_count = graphene.Int(description="People who currently hold this role.")

    class Meta:
        model = Role
        fields = ("uid", "name", "description", "is_system", "active", "created_at")

    def resolve_permissions(self, info):
        return [link.permission for link in self.role_permissions.all()]

    def resolve_permission_count(self, info):
        return len(self.role_permissions.all())

    def resolve_user_count(self, info):
        annotated = getattr(self, "users_count", None)
        return annotated if annotated is not None else self.role_users.filter(deleted=False).count()
