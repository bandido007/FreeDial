from django import forms
from django.contrib import admin

from apps.core.exceptions import ApplicationError
from apps.queues import services
from apps.queues.models import EntryPoint, Queue, QueueMember, SkillRule, SkillRuleSet
from apps.queues.permissions import EDIT_QUEUES, VIEW_QUEUES
from apps.rbac.admin_access import RbacAdminMixin


class QueueForm(forms.ModelForm):
    class Meta:
        model = Queue
        fields = (
            "name",
            "strategy",
            "skill_rule_set",
            "ring_timeout",
            "retry_delay",
            "max_wait",
            "max_callers",
            "overflow_queue",
            "priority",
            "wrapup_seconds",
            "service_level_seconds",
            "service_level_percent",
            "moh_class",
            "active",
        )

    def clean(self):
        data = super().clean()
        try:
            services.validate_queue(
                name=data.get("name"),
                except_pk=self.instance.pk,
                **{field: data.get(field) for field in services.LIMITS},
            )
            services.validate_overflow(
                self.instance if self.instance.pk else None, data.get("overflow_queue")
            )
        except ApplicationError as exc:
            raise forms.ValidationError(exc.message) from exc
        return data


class MemberInline(RbacAdminMixin, admin.TabularInline):
    view_permission = VIEW_QUEUES
    manage_permission = EDIT_QUEUES
    model = QueueMember
    extra = 1
    autocomplete_fields = ("agent",)

    def has_add_permission(self, request, obj=None):  # an inline's version takes obj
        return self._holds(request, self.manage_permission)


@admin.register(Queue)
class QueueAdmin(RbacAdminMixin, admin.ModelAdmin):
    view_permission = VIEW_QUEUES
    manage_permission = EDIT_QUEUES
    form = QueueForm
    inlines = [MemberInline]
    list_display = ("name", "strategy", "skill_rule_set", "max_wait", "overflow_queue", "today", "active")

    @admin.display(description="Today (service level)")
    def today(self, obj):
        from apps.queues.queries import queue_stats

        stats = queue_stats(obj)
        if not stats["offered"]:
            return "no calls yet"
        return (
            f"{stats['service_level_percent']:.0f}% in {obj.service_level_seconds}s "
            f"(target {obj.service_level_percent}%) · {stats['answered']}/{stats['offered']} answered"
        )

    search_fields = ("name",)

    def delete_model(self, request, obj):
        services.delete_queue(uid=obj.uid)

    def delete_queryset(self, request, queryset):
        for queue in queryset:
            services.delete_queue(uid=queue.uid)


class EntryPointForm(forms.ModelForm):
    class Meta:
        model = EntryPoint
        fields = ("number", "name", "queue", "active")

    def clean_number(self):
        try:
            return services.validate_entry_point(
                number=self.cleaned_data.get("number"), except_pk=self.instance.pk
            )
        except ApplicationError as exc:
            raise forms.ValidationError(exc.message) from exc


@admin.register(EntryPoint)
class EntryPointAdmin(RbacAdminMixin, admin.ModelAdmin):
    view_permission = VIEW_QUEUES
    manage_permission = EDIT_QUEUES
    form = EntryPointForm
    list_display = ("number", "name", "queue", "active")
    search_fields = ("number", "name")

    def delete_queryset(self, request, queryset):
        for entry in queryset:
            entry.delete()  # soft delete, one by one (a queryset delete would be a hard delete)


class SkillRuleForm(forms.ModelForm):
    class Meta:
        model = SkillRule
        fields = ("after_seconds", "condition")

    def clean_condition(self):
        try:
            return services.validate_condition(self.cleaned_data.get("condition"))
        except ApplicationError as exc:
            raise forms.ValidationError(exc.message) from exc


class SkillRuleInline(RbacAdminMixin, admin.TabularInline):
    """From this many seconds of waiting on, agents must match the condition (the last rule reached applies)."""

    view_permission = VIEW_QUEUES
    manage_permission = EDIT_QUEUES
    model = SkillRule
    form = SkillRuleForm
    extra = 1

    def has_add_permission(self, request, obj=None):
        return self._holds(request, self.manage_permission)


@admin.register(SkillRuleSet)
class SkillRuleSetAdmin(RbacAdminMixin, admin.ModelAdmin):
    view_permission = VIEW_QUEUES
    manage_permission = EDIT_QUEUES
    fields = ("name", "active")
    inlines = [SkillRuleInline]
    list_display = ("name", "rules_summary", "active")
    search_fields = ("name",)

    @admin.display(description="Rules")
    def rules_summary(self, obj):
        return " → ".join(f"{rule.after_seconds}s: {rule.condition}" for rule in obj.rules.all())

    def delete_queryset(self, request, queryset):
        for row in queryset:
            row.delete()
