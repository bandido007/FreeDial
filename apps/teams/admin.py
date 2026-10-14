from django import forms
from django.contrib import admin

from apps.core.exceptions import ApplicationError
from apps.rbac.admin_access import RbacAdminMixin
from apps.teams import services
from apps.teams.models import Team
from apps.teams.permissions import MANAGE_TEAMS, VIEW_TEAMS


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ("name", "description", "supervisors", "agents")

    def clean(self):
        data = super().clean()
        try:
            services.check_members(data.get("supervisors") or [], data.get("agents") or [])
        except ApplicationError as exc:
            raise forms.ValidationError(exc.message) from exc
        return data


@admin.register(Team)
class TeamAdmin(RbacAdminMixin, admin.ModelAdmin):
    view_permission = VIEW_TEAMS
    manage_permission = MANAGE_TEAMS
    form = TeamForm
    list_display = ("name", "updated_at")
    search_fields = ("name", "description")
    filter_horizontal = ("supervisors", "agents")

    def save_model(self, request, obj, form, change):
        data = form.cleaned_data
        if change:
            team = services.update_team(
                uid=obj.uid, actor=request.user, name=data["name"], description=data["description"]
            )
        else:
            team = services.create_team(
                actor=request.user, name=data["name"], description=data["description"]
            )
        obj.pk, obj.uid = team.pk, team.uid
        obj.refresh_from_db()

    def save_related(self, request, form, formsets, change):
        # Members go through the service too (it checks nobody holds both roles).
        services.update_team(
            uid=form.instance.uid,
            actor=request.user,
            supervisors=form.cleaned_data["supervisors"],
            agents=form.cleaned_data["agents"],
        )

    def delete_model(self, request, obj):
        services.delete_team(uid=obj.uid)

    def delete_queryset(self, request, queryset):
        for team in queryset:
            services.delete_team(uid=team.uid)
