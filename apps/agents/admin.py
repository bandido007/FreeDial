from django import forms
from django.contrib import admin

from apps.agents import services
from apps.agents.models import Agent, AgentSession, AgentSkill, NotReadyReason, Skill, WrapupCode
from apps.agents.permissions import MANAGE_AGENTS, VIEW_AGENTS
from apps.core.exceptions import ApplicationError
from apps.rbac.admin_access import RbacAdminMixin


class AgentForm(forms.ModelForm):
    class Meta:
        model = Agent
        fields = ("number", "name", "user", "phone", "active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        first, last = services.number_range()
        self.fields["number"].help_text = (
            f"Suggested: the next free number from {first} to {last} (the range is in System settings). "
            "You can type another; a number already used is refused."
        )

    def clean(self):
        data = super().clean()
        try:
            services.validate_agent(
                number=data.get("number"),
                name=data.get("name"),
                user=data.get("user"),
                except_pk=self.instance.pk,
            )
            services.validate_phone(data.get("phone"))
        except ApplicationError as exc:
            raise forms.ValidationError(exc.message) from exc
        return data


class AgentSkillInline(RbacAdminMixin, admin.TabularInline):
    """The agent's skills and levels (1 to 10), which skill rules match against."""

    view_permission = VIEW_AGENTS
    manage_permission = MANAGE_AGENTS
    model = AgentSkill
    extra = 1
    autocomplete_fields = ("skill",)

    def has_add_permission(self, request, obj=None):  # an inline's version takes obj
        return self._holds(request, self.manage_permission)


@admin.register(Agent)
class AgentAdmin(RbacAdminMixin, admin.ModelAdmin):
    view_permission = VIEW_AGENTS
    manage_permission = MANAGE_AGENTS
    form = AgentForm
    inlines = [AgentSkillInline]
    list_display = ("number", "name", "user", "takes_calls_on", "skill_levels", "active")
    search_fields = ("number", "name", "user__email")
    autocomplete_fields = ("user",)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        initial.setdefault("number", services.next_free_number())
        return initial

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
                phone=data["phone"],
                clear_phone=data["phone"] is None,
            )
            if agent.active != data["active"]:
                agent.activate() if data["active"] else agent.deactivate()
        else:
            agent = services.create_agent(
                actor=request.user,
                number=data["number"],
                name=data["name"],
                user=data["user"],
                phone=data["phone"],
            )
        obj.pk, obj.uid = agent.pk, agent.uid
        obj.refresh_from_db()

    @admin.display(description="Takes calls on")
    def takes_calls_on(self, obj):
        return f"Desk phone {obj.phone.sip_id}" if obj.phone_id else "Browser"

    @admin.display(description="Skills")
    def skill_levels(self, obj):
        return ", ".join(f"{row.skill.name} {row.level}" for row in obj.skills.select_related("skill"))

    def delete_model(self, request, obj):
        services.delete_agent(uid=obj.uid)

    def delete_queryset(self, request, queryset):
        for agent in queryset:
            services.delete_agent(uid=agent.uid)


@admin.register(NotReadyReason, WrapupCode)
class PickListAdmin(RbacAdminMixin, admin.ModelAdmin):
    """Not Ready reasons and wrap-up codes: names the agent page offers."""

    view_permission = VIEW_AGENTS
    manage_permission = MANAGE_AGENTS
    fields = ("name", "active")
    list_display = ("name", "active")
    search_fields = ("name",)

    def get_fields(self, request, obj=None):
        return ("name", "max_seconds", "active") if self.model is NotReadyReason else self.fields

    def delete_queryset(self, request, queryset):
        for row in queryset:
            row.delete()  # soft delete, one by one


@admin.register(AgentSession)
class AgentSessionAdmin(RbacAdminMixin, admin.ModelAdmin):
    view_permission = VIEW_AGENTS
    manage_permission = "-"  # written by the agent page only
    list_display = ("agent", "endpoint", "login_at", "logout_at")
    list_filter = ("agent",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SkillForm(forms.ModelForm):
    class Meta:
        model = Skill
        fields = ("name", "kind", "description", "active")

    def clean_name(self):
        try:
            return services.validate_skill_name(self.cleaned_data.get("name"), except_pk=self.instance.pk)
        except ApplicationError as exc:
            raise forms.ValidationError(exc.message) from exc


@admin.register(Skill)
class SkillAdmin(RbacAdminMixin, admin.ModelAdmin):
    view_permission = VIEW_AGENTS
    manage_permission = MANAGE_AGENTS
    form = SkillForm
    list_display = ("name", "kind", "description", "active")
    list_filter = ("kind",)
    search_fields = ("name", "description")

    def delete_queryset(self, request, queryset):
        for row in queryset:
            row.delete()
