"""Django admin pages guarded by rbac permissions (project addition, not from the starter).

The starter's admin uses Django's own model permissions, which AlgoDial never
grants. A ModelAdmin that mixes this in asks rbac instead, so the same role
that allows a GraphQL mutation also opens the admin page. Superusers keep
full access.
"""

from typing import TYPE_CHECKING, ClassVar

from apps.core.admin_workspace import WorkspaceAdminMixin
from apps.core.hooks import resolve_permissions

if TYPE_CHECKING:
    from django.template.response import _TemplateForResponseT


class RbacAdminMixin(WorkspaceAdminMixin):
    change_form_template: ClassVar["_TemplateForResponseT | None"] = "admin/workspace_change_form.html"
    view_permission: str = ""
    manage_permission: str = ""

    def _holds(self, request, code: str) -> bool:
        user = request.user
        if not (user.is_active and user.is_staff):
            return False
        return user.is_superuser or code in resolve_permissions(user, request)

    def has_module_permission(self, request):
        return self._holds(request, self.view_permission) or self._holds(request, self.manage_permission)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self._holds(request, self.manage_permission)

    def has_change_permission(self, request, obj=None):
        return self._holds(request, self.manage_permission)

    def has_delete_permission(self, request, obj=None):
        return self._holds(request, self.manage_permission)
