"""Admin registrations for the notifications domain."""

from django.contrib import admin

from apps.notifications.models import (
    EmailConfiguration,
    Notification,
    NotificationPreference,
    NotificationTemplate,
    SmsConfiguration,
)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "channel",
        "category",
        "status",
        "email_to",
        "phone_to",
        "attempts",
        "is_sent",
        "created_at",
    )
    list_filter = ("channel", "status", "category", "is_sent", "is_read")
    search_fields = ("email_to", "phone_to", "full_name", "subject")
    # Delivery log is system-written; keep it read-only in the admin.
    readonly_fields = [f.name for f in Notification._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(EmailConfiguration)
class EmailConfigurationAdmin(admin.ModelAdmin):
    list_display = ("name", "host", "port", "from_email", "use_tls", "verify_tls", "is_default", "active")
    list_filter = ("is_default", "use_tls", "use_ssl", "active")
    search_fields = ("name", "host", "from_email")
    # Never surface the encrypted secret column in the admin form.
    exclude = ("password_encrypted",)


@admin.register(SmsConfiguration)
class SmsConfigurationAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "api_url", "sender_id", "is_default", "active")
    list_filter = ("provider", "is_default", "active")
    search_fields = ("name", "api_url", "sender_id")
    exclude = ("api_key_encrypted",)


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "channel", "is_active", "active")
    list_filter = ("category", "channel", "is_active")
    search_fields = ("name", "subject", "body")


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "category", "channel", "enabled")
    list_filter = ("channel", "enabled")
    search_fields = ("user__email", "category")
    autocomplete_fields = ("user",)
