"""A shared record browser beside Django's original admin forms.

Permissions, form validation, CSRF, inline saves and delete confirmations remain
with ModelAdmin. Read-only logs retain their table view. No object is selected
or changed automatically when somebody opens a list.
"""

from urllib.parse import urlencode

from django.contrib import admin
from django.contrib.admin.widgets import AdminSplitDateTime
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.dispatch import Signal
from django.template.response import TemplateResponse
from django.urls import reverse

# Sent when an admin form comes back with errors (UX_PRINCIPLES.md section 6: "validation errors shown").
# Arguments: model ("app_label.model_name") and fields (names only, never the values typed).
admin_form_refused = Signal()


class WorkspaceAdminMixin:
    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if field is not None and isinstance(field.widget, AdminSplitDateTime):
            for widget, part in zip(field.widget.widgets, ("date", "time"), strict=True):
                widget.attrs["aria-label"] = f"{db_field.verbose_name} {part}"
        return field

    def workspace_context(self, request, object_id=None):
        query = request.GET.get("workspace_q", "").strip()
        rows = self.get_queryset(request)
        if query:
            rows, duplicates = self.get_search_results(request, rows, query)
            if duplicates:
                rows = rows.distinct()
        page = Paginator(rows, 25).get_page(request.GET.get("workspace_page", 1))
        name = f"{self.opts.app_label}_{self.opts.model_name}"
        namespace = self.admin_site.name
        search = "?" + urlencode({"workspace_q": query}) if query else ""
        return {
            "workspace_records": [
                {
                    "label": str(obj),
                    "url": reverse(f"{namespace}:{name}_change", args=[obj.pk]) + search,
                    "selected": str(obj.pk) == str(object_id),
                }
                for obj in page
            ],
            "workspace_page": page,
            "workspace_query": query,
            "workspace_list_url": reverse(f"{namespace}:{name}_changelist"),
            "workspace_add_url": reverse(f"{namespace}:{name}_add")
            if self.has_add_permission(request)
            else None,
            "workspace_title": self.opts.verbose_name_plural,
        }

    def changelist_view(self, request, extra_context=None):
        # Full tables remain available for filters, exports and bulk actions.
        workspace_params = {"workspace_q", "workspace_page"}
        editor = self.has_add_permission(request) or self.has_change_permission(request)
        if not editor or request.method != "GET" or set(request.GET) - workspace_params:
            original = request.GET
            if "table" in request.GET:
                request.GET = request.GET.copy()
                request.GET.pop("table")
            try:
                return super().changelist_view(request, extra_context)
            finally:
                request.GET = original
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        context = {
            **self.admin_site.each_context(request),
            **self.workspace_context(request),
            "opts": self.opts,
            "title": str(self.opts.verbose_name_plural).capitalize(),
            **(extra_context or {}),
        }
        return TemplateResponse(request, "admin/workspace_list.html", context)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        context = dict(extra_context or {})
        if not request.GET.get("_popup") and self.has_view_or_change_permission(request):
            context.update(self.workspace_context(request, object_id))
        response = super().changeform_view(request, object_id, form_url, context)
        form = getattr((getattr(response, "context_data", None) or {}).get("adminform"), "form", None)
        if request.method == "POST" and form is not None and form.errors:
            admin_form_refused.send(
                sender=type(self),
                model=f"{self.opts.app_label}.{self.opts.model_name}",
                fields=sorted(form.errors),
            )
        return response


class WorkspaceModelAdmin(WorkspaceAdminMixin, admin.ModelAdmin):
    change_form_template = "admin/workspace_change_form.html"
