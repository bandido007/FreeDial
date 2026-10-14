"""Admin registrations for the audit domain (read-only: these are logs)."""

from django.contrib import admin

from apps.audit.models import AuditLog, ErrorReportSubscription, RequestLog


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "actor_email", "action", "entity", "object_repr")
    list_filter = ("action", "app_label")
    search_fields = ("actor_email", "object_repr", "entity", "request_id")
    date_hierarchy = "created_at"


@admin.register(RequestLog)
class RequestLogAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "level", "kind", "operations", "status_code", "duration_ms", "user_email")
    list_filter = ("level", "kind", "is_resolved")
    search_fields = ("operations", "path", "user_email", "error_type", "request_id")
    date_hierarchy = "created_at"


@admin.register(ErrorReportSubscription)
class ErrorReportSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at")
    search_fields = ("user__email",)
    autocomplete_fields = ("user",)
