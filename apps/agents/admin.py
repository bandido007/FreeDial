from django import forms
from django.contrib import admin

from apps.agents import services
from apps.agents.models import Agent
from apps.agents.permissions import MANAGE_AGENTS, VIEW_AGENTS
from apps.core.exceptions import ApplicationError
from apps.rbac.admin_access import RbacAdminMixin


class AgentForm(forms.ModelForm):
    class Meta:
        model = Agent
        fields = ("number", "name", "user", "active")

    def clean(self):
        data = super().clean()
        try:
            services.validate_agent(
                number=data.get("number"),
                name=data.get("name"),
                user=data.get("user"),
                except_pk=self.instance.pk,
            )
        except ApplicationError as exc:
            raise forms.ValidationError(exc.message) from exc
        return data


@admin.register(Agent)
class AgentAdmin(RbacAdminMixin, admin.ModelAdmin):
    view_permission = VIEW_AGENTS
    manage_permission = MANAGE_AGENTS
    form = AgentForm
    list_display = ("number", "name", "user", "active")
    search_fields = ("number", "name", "user__email")
    autocomplete_fields = ("user",)

    def save_model(self, request, obj, form, change):
        data = form.cleaned_data
        if change:
            agent = services.update_agent(
                uid=obj.uid,
                actor=request.user,
                number=data["number"],
                name=data["name"],
                user=data["user"],
                clear_user=data["user"] is None,
            )
            if agent.active != data["active"]:
                agent.activate() if data["active"] else agent.deactivate()
        else:
            agent = services.create_agent(
                actor=request.user, number=data["number"], name=data["name"], user=data["user"]
            )
        obj.pk, obj.uid = agent.pk, agent.uid
        obj.refresh_from_db()

    def delete_model(self, request, obj):
        services.delete_agent(uid=obj.uid)

    def delete_queryset(self, request, queryset):
        for agent in queryset:
            services.delete_agent(uid=agent.uid)
