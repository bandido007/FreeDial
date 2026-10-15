"""Admin -> Setup guide: the setup checklist and the user manual on one page.

``SetupConfirmation`` gives the page its place in the admin (like Reports); its list is replaced by
the guide, and two small POST views confirm or clear a hands-on test.
"""

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.views.decorators.http import require_POST

from apps.core.exceptions import ApplicationError
from apps.rbac.admin_access import RbacAdminMixin
from apps.rbac.models import Role
from apps.setup import checklist, services
from apps.setup.models import SetupConfirmation
from apps.setup.permissions import CONFIRM_SETUP_TESTS, VIEW_SETUP_GUIDE


@admin.register(SetupConfirmation)
class SetupGuideAdmin(RbacAdminMixin, admin.ModelAdmin):
    view_permission = VIEW_SETUP_GUIDE
    manage_permission = "-"  # the guide is read; confirmations go through the two views below

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        wrap = self.admin_site.admin_view
        return [
            path("confirm/", wrap(require_POST(self.confirm_view)), name="setup_confirm"),
            path("clear/", wrap(require_POST(self.clear_view)), name="setup_clear"),
            *super().get_urls(),
        ]

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        sections = checklist.build()
        done, counted, attention = checklist.progress(sections)
        context = {
            **self.admin_site.each_context(request),
            "title": "Setup guide",
            "opts": self.model._meta,
            "sections": sections,
            "done": done,
            "counted": counted,
            "attention": attention,
            "can_confirm": self._holds(request, CONFIRM_SETUP_TESTS),
            "roles": [  # read live, so the manual's table of roles is never out of date
                (role, sorted(link.permission.name for link in role.role_permissions.all()))
                for role in Role.objects.filter(active=True)
                .prefetch_related("role_permissions__permission")
                .order_by("name")
            ],
        }
        return TemplateResponse(request, "setup/guide.html", context)

    def _act(self, request, action, done_message):
        if not self._holds(request, CONFIRM_SETUP_TESTS):
            raise PermissionDenied
        check = request.POST.get("check", "")
        try:
            action(check)
            messages.success(request, done_message)
        except ApplicationError as exc:
            messages.error(request, exc.message)
        url = reverse(f"{self.admin_site.name}:setup_setupconfirmation_changelist")
        return HttpResponseRedirect(f"{url}#check-{check}")

    def confirm_view(self, request):
        return self._act(
            request,
            lambda check: services.confirm(
                actor=request.user, check=check, note=request.POST.get("note", "")
            ),
            "Confirmed. Thank you.",
        )

    def clear_view(self, request):
        return self._act(
            request,
            lambda check: services.clear(actor=request.user, check=check),
            "Cleared: test it again when you can.",
        )
