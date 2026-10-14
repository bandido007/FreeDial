from urllib.parse import urlencode

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse

from apps.configuration import services
from apps.configuration.models import SystemSetting
from apps.core.admin_workspace import WorkspaceModelAdmin
from apps.core.catalogue import SETTING_BOOLEAN, SETTING_CHOICE, SETTING_INTEGER, SETTING_LIST
from apps.core.exceptions import ValidationError


def setting_form(row, data=None):
    """Use the declared type, limits and choices instead of exposing raw JSON."""
    kind = row["type"]
    options = {"label": row["name"], "help_text": row["description"], "required": False}
    if kind == SETTING_INTEGER:
        field = forms.IntegerField(min_value=row["minimum"], max_value=row["maximum"], **options)
    elif kind == SETTING_BOOLEAN:
        field = forms.TypedChoiceField(
            choices=[("true", "Yes"), ("false", "No")], coerce=lambda v: v == "true", **options
        )
    elif kind == SETTING_CHOICE:
        field = forms.ChoiceField(choices=[(v, v) for v in row["choices"]], **options)
    elif kind == SETTING_LIST and row["choices"]:
        field = forms.MultipleChoiceField(choices=[(v, v) for v in row["choices"]], **options)
    else:
        field = forms.CharField(max_length=row["maximum"], **options)
    initial = row["value"]
    if kind == SETTING_BOOLEAN:
        initial = "true" if initial else "false"
    elif kind == SETTING_LIST and not row["choices"]:
        initial = ", ".join(initial or [])
    form = forms.Form(data=data, initial={"value": initial})
    form.fields["value"] = field
    return form


@admin.register(SystemSetting)
class SystemSettingAdmin(WorkspaceModelAdmin):
    list_display = ("key", "value", "updated_by", "updated_at")
    search_fields = ("key",)
    readonly_fields = ("key",)

    def has_add_permission(self, request):
        return False

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        rows = services.all_settings()
        key = request.GET.get("key", "")
        selected = next((row for row in rows if row["key"] == key), None)
        can_change = self.has_change_permission(request)
        form = (
            setting_form(selected, request.POST if request.method == "POST" else None) if selected else None
        )
        if request.method == "POST":
            if not can_change or selected is None:
                raise PermissionDenied
            if request.POST.get("action") == "reset":
                services.reset_value(key=key, actor=request.user)
                self.message_user(request, "Default restored.", messages.SUCCESS)
                return HttpResponseRedirect(request.get_full_path())
            if form.is_valid():
                try:
                    services.set_value(key=key, value=form.cleaned_data["value"], actor=request.user)
                except ValidationError as error:
                    form.add_error("value", error.message)
                else:
                    self.message_user(request, "Setting saved.", messages.SUCCESS)
                    return HttpResponseRedirect(request.get_full_path())
        if form and not can_change:
            form.fields["value"].disabled = True
        query = request.GET.get("workspace_q", "").strip()
        url = reverse(f"{self.admin_site.name}:configuration_systemsetting_changelist")
        context = {
            **self.admin_site.each_context(request),
            "opts": self.opts,
            "title": "System settings",
            "workspace_title": "System settings",
            "workspace_query": query,
            "workspace_list_url": url,
            "workspace_hide_table": True,
            "workspace_records": [
                {
                    "label": row["name"],
                    "url": url + "?" + urlencode({"key": row["key"], "workspace_q": query}),
                    "selected": row["key"] == key,
                }
                for row in rows
                if not query or query.lower() in (row["name"] + row["group"] + row["key"]).lower()
            ],
            "selected_setting": selected,
            "setting_form": form,
            "can_change_setting": can_change,
            **(extra_context or {}),
        }
        return TemplateResponse(request, "admin/system_settings.html", context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        # Audit/history links to saved overrides lead to the same validated editor.
        obj = self.get_object(request, object_id)
        if obj is None or not self.has_view_or_change_permission(request, obj):
            raise PermissionDenied
        return HttpResponseRedirect(
            reverse(f"{self.admin_site.name}:configuration_systemsetting_changelist")
            + "?"
            + urlencode({"key": obj.key})
        )
