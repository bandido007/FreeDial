"""Read-only pages: the writer is the only one who writes these tables."""

from django.contrib import admin
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from apps.calls.models import (
    Call,
    CallbackStatus,
    CallbackTask,
    Event,
    InteractionPart,
    Leg,
    RecordingStatus,
    Transfer,
)
from apps.calls.permissions import HANDLE_CALLBACKS, LISTEN_RECORDINGS, VIEW_CALLS
from apps.core.hooks import resolve_permissions
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


class TransferInline(admin.TabularInline):
    model = Transfer
    extra = 0
    can_delete = False
    fields = (
        "kind",
        "target_kind",
        "target",
        "by_agent_uid",
        "status",
        "reason",
        "started_at",
        "answered_at",
        "ended_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class InteractionPartInline(admin.StackedInline):
    """Who served the caller, in order (M8): the AI agent with its summary and transcript, then a person."""

    model = InteractionPart
    extra = 0
    can_delete = False
    verbose_name_plural = "Interaction"
    fields = (
        "kind",
        "started_at",
        "ended_at",
        "who",
        "outcome",
        "reason",
        "summary",
        "conversation",
        "wrapup_code",
    )
    readonly_fields = fields

    @admin.display(description="Who")
    def who(self, obj):
        return obj.agent_uid or obj.ai_agent_uid

    @admin.display(description="Transcript")
    def conversation(self, obj):
        if not obj.transcript:
            return "-"
        rows = format_html_join(
            "",
            '<div><span style="color:#888">{} s</span> <b>{}</b>: {}</div>',
            (
                (turn.get("at", ""), "Caller" if turn.get("who") == "caller" else "AI", turn.get("text", ""))
                for turn in obj.transcript
            ),
        )
        return format_html("<div>{}</div>", rows)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Call)
class CallAdmin(ReadOnly, admin.ModelAdmin):
    list_display = (
        "linkedid",
        "entry",
        "queue",
        "caller",
        "started_at",
        "answered_at",
        "ended_at",
        "result",
        "recording_status",
    )
    list_filter = ("result", "queue", "recording_status")
    search_fields = ("linkedid", "caller", "agent_uid")
    inlines = [InteractionPartInline, LegInline, TransferInline]
    fields = (
        "linkedid",
        "entry",
        "queue",
        "caller",
        "caller_name",
        "started_at",
        "answered_at",
        "ended_at",
        "result",
        "end_reason",
        "agent_uid",
        "recording_status",
        "recording_seconds",
        "recording_player",
    )
    readonly_fields = fields

    def get_fields(self, request, obj=None):
        """The player only for people allowed to listen (the file view checks it again)."""
        fields = list(super().get_fields(request, obj))
        user = request.user
        if not (user.is_superuser or LISTEN_RECORDINGS in resolve_permissions(user, request)):
            fields.remove("recording_player")
        return fields

    @admin.display(description="Recording")
    def recording_player(self, obj):
        if obj.recording_status != RecordingStatus.READY:
            return obj.get_recording_status_display()
        url = reverse("call_recording", args=[obj.pk])
        return format_html(
            '<audio controls preload="none" src="{}"></audio><br>'
            "Stereo: left = the customer, right = everything they heard.",
            url,
        )


@admin.register(Event)
class EventAdmin(ReadOnly, admin.ModelAdmin):
    list_display = ("at", "type", "call_id", "agent_uid")
    list_filter = ("type",)
    search_fields = ("call_id", "agent_uid", "leg_id", "event_id")


@admin.register(CallbackTask)
class CallbackTaskAdmin(RbacAdminMixin, admin.ModelAdmin):
    """Callers who overflowed to voicemail: listen to the message, call back, mark it done."""

    view_permission = HANDLE_CALLBACKS
    manage_permission = HANDLE_CALLBACKS
    list_display = ("created_at", "caller", "caller_name", "queue", "language", "duration", "status")
    list_filter = ("status", "queue")
    search_fields = ("caller", "caller_name", "linkedid")
    fields = (
        "caller",
        "caller_name",
        "queue",
        "language",
        "created_at",
        "message",
        "status",
        "note",
        "done_at",
        "done_by",
    )
    readonly_fields = (
        "caller",
        "caller_name",
        "queue",
        "language",
        "created_at",
        "message",
        "done_at",
        "done_by",
    )
    actions = ["mark_called_back"]

    @admin.display(description="Message")
    def message(self, obj):
        if not obj.recording:
            return "No message (hung up before the tone)"
        url = reverse("callback_recording", args=[obj.pk])
        return format_html('<audio controls preload="none" src="{}"></audio> {} s', url, obj.duration or "?")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if obj.status != CallbackStatus.OPEN and obj.done_at is None:
            obj.done_at, obj.done_by = timezone.now(), request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Mark called back")
    def mark_called_back(self, request, queryset):
        queryset.filter(status=CallbackStatus.OPEN).update(
            status=CallbackStatus.DONE, done_at=timezone.now(), done_by=request.user
        )
