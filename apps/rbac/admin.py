from django.contrib import admin

from apps.core.admin_workspace import WorkspaceModelAdmin
from apps.rbac.models import Permission, PermissionGroup, Role, RolePermission, UserRole


@admin.register(PermissionGroup)
class PermissionGroupAdmin(WorkspaceModelAdmin):
    list_display = ("name", "app_label", "description")
    search_fields = ("name",)


@admin.register(Permission)
class PermissionAdmin(WorkspaceModelAdmin):
    """Read-only: permissions are declared in code and written by sync_rbac."""

    list_display = ("code", "name", "level", "group")
    list_filter = ("level", "group")
    search_fields = ("code", "name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class RolePermissionInline(admin.TabularInline):
    model = RolePermission
    extra = 0
    autocomplete_fields = ("permission",)
    fields = ("permission",)


@admin.register(Role)
class RoleAdmin(WorkspaceModelAdmin):
    list_display = ("name", "is_system", "active", "created_at")
    list_filter = ("is_system", "active")
    search_fields = ("name",)
    inlines = [RolePermissionInline]


@admin.register(UserRole)
class UserRoleAdmin(WorkspaceModelAdmin):
    list_display = ("user", "role", "source", "expires_at", "created_at")
    list_filter = ("role", "source")
    search_fields = ("user__email", "role__name")
    autocomplete_fields = ("user", "role")
