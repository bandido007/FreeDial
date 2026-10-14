from django.contrib import admin

from apps.ai.models import AiAgent, AiRoute
from apps.ai.permissions import EDIT_AI_AGENTS, VIEW_AI_AGENTS
from apps.rbac.admin_access import RbacAdminMixin


class AiRouteInline(RbacAdminMixin, admin.TabularInline):
    view_permission = VIEW_AI_AGENTS
    manage_permission = EDIT_AI_AGENTS
    model = AiRoute
    extra = 0
    fields = ("position", "queue", "when", "skill", "min_level")
    verbose_name = "route"
    verbose_name_plural = "Where it may send callers (the AI picks one; the brain checks it)"

    def has_add_permission(self, request, obj=None):  # an inline's version takes obj
        return self._holds(request, self.manage_permission)


@admin.register(AiAgent)
class AiAgentAdmin(RbacAdminMixin, admin.ModelAdmin):
    view_permission = VIEW_AI_AGENTS
    manage_permission = EDIT_AI_AGENTS
    list_display = ("name", "voice", "llm_model", "max_failed_turns", "active")
    search_fields = ("name",)
    inlines = [AiRouteInline]
    fieldsets = (
        (None, {"fields": ("name", "active")}),
        ("What it knows", {"fields": ("instructions",)}),
        ("What it says first", {"fields": ("greeting_swahili", "greeting_english")}),
        (
            "When a person takes over",
            {
                "fields": ("max_failed_turns", "sensitive_topics", "start_timeout"),
                "description": "Also always: when the caller asks for a person, and when the AI decides it.",
            },
        ),
        (
            "Voice and models",
            {
                "fields": ("voice", "stt_model", "llm_model", "tts_model"),
                "description": "Defaults: the bake-off's winners (docs/AI_BAKEOFF.md).",
            },
        ),
    )

    def delete_queryset(self, request, queryset):
        for agent in queryset:
            agent.delete()  # soft delete, one by one (a queryset delete would be a hard delete)
