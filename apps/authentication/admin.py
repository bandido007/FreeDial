"""Admin registrations for the authentication domain."""

from django.contrib import admin

from apps.authentication.models import (
    AuthenticationAudit,
    ExternalIdentity,
    LdapConfig,
    MfaCredential,
    PasswordResetToken,
    UserLoginHistory,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """For logs: nothing is added, changed or removed by hand."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuthenticationAudit)
class AuthenticationAuditAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "email", "event_type", "status", "ip_address")
    list_filter = ("event_type", "status")
    search_fields = ("email", "ip_address", "request_id")
    date_hierarchy = "created_at"


@admin.register(UserLoginHistory)
class UserLoginHistoryAdmin(ReadOnlyAdmin):
    list_display = ("logged_in_at", "user", "ip_address", "browser", "os", "provider", "is_new_device")
    list_filter = ("provider", "is_new_device")
    search_fields = ("user__email", "ip_address")
    date_hierarchy = "logged_in_at"


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(ReadOnlyAdmin):
    list_display = ("user", "purpose", "is_used", "expires_at", "created_at")
    list_filter = ("purpose", "is_used")
    search_fields = ("user__email",)
    exclude = ("token_hash",)


@admin.register(MfaCredential)
class MfaCredentialAdmin(ReadOnlyAdmin):
    list_display = ("user", "method", "confirmed_at", "last_used_at")
    search_fields = ("user__email",)
    exclude = ("secret_encrypted",)


@admin.register(ExternalIdentity)
class ExternalIdentityAdmin(ReadOnlyAdmin):
    list_display = ("user", "provider", "email", "last_login_at")
    list_filter = ("provider",)
    search_fields = ("user__email", "email")


@admin.register(LdapConfig)
class LdapConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "ldap_url", "email_domain", "default_role", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "ldap_url", "email_domain")
    exclude = ("bind_password_encrypted",)
