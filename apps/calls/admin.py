"""Read-only pages: the writer is the only one who writes these tables."""

from django.contrib import admin

from apps.calls.models import Call, Event, Leg
from apps.calls.permissions import VIEW_CALLS
from apps.rbac.admin_access import RbacAdminMixin


class ReadOnly(RbacAdminMixin):
    view_permission = VIEW_CALLS
    manage_permission = "-"  # nobody edits what the writer records

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class LegInline(admin.TabularInline):
    model = Leg
    extra = 0
    can_delete = False
    fields = (
        "uniqueid",
        "role",
        "endpoint",
        "agent_uid",
        "started_at",
        "answered_at",
        "ended_at",
        "outcome",
        "cause",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Call)
class CallAdmin(ReadOnly, admin.ModelAdmin):
    list_display = ("linkedid", "entry", "queue", "caller", "started_at", "answered_at", "ended_at", "result")
    list_filter = ("result", "queue")
    search_fields = ("linkedid", "caller", "agent_uid")
    inlines = [LegInline]


@admin.register(Event)
class EventAdmin(ReadOnly, admin.ModelAdmin):
    list_display = ("at", "type", "call_id", "agent_uid")
    list_filter = ("type",)
    search_fields = ("call_id", "agent_uid", "leg_id", "event_id")
