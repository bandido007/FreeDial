"""Admin registrations for the users domain."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.core.admin_workspace import WorkspaceAdminMixin, WorkspaceModelAdmin
from apps.users.models import OtpVerification, User, UserConsent


@admin.register(User)
class UserAdmin(WorkspaceAdminMixin, DjangoUserAdmin):
    change_form_template = "admin/workspace_change_form.html"
    ordering = ("-date_joined",)
    list_display = (
        "email",
        "first_name",
        "last_name",
        "account_type",
        "account_status",
        "is_active",
        "is_verified",
        "mfa_enabled",
    )
    list_filter = (
        "account_type",
        "auth_provider",
        "account_status",
        "is_active",
        "is_verified",
        "mfa_enabled",
        "deleted",
    )
    search_fields = ("email", "first_name", "last_name", "phone")
    readonly_fields = ("uid", "date_joined", "updated_at", "last_login", "password_changed_at", "invited_at")

    fieldsets = (
        (None, {"fields": ("uid", "email", "password")}),
        (
            "Personal details",
            {
                "fields": (
                    "first_name",
                    "middle_name",
                    "last_name",
                    "phone",
                    "gender",
                    "date_of_birth",
                    "photo",
                )
            },
        ),
        (
            "Account",
            {
                "fields": (
                    "account_type",
                    "auth_provider",
                    "account_status",
                    "is_active",
                    "is_verified",
                    "phone_verified",
                    "mfa_enabled",
                    "must_change_password",
                )
            },
        ),
        ("Management access", {"fields": ("is_staff", "is_superuser")}),
        (
            "Dates",
            {"fields": ("date_joined", "last_login", "password_changed_at", "invited_at", "updated_at")},
        ),
        ("Deletion", {"fields": ("deleted", "deleted_at", "deactivated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "first_name", "last_name", "account_type", "password1", "password2"),
            },
        ),
    )

    def get_queryset(self, request):
        # The admin is the restore surface: show soft-deleted accounts too.
        return User.all_objects.all()


@admin.register(UserConsent)
class UserConsentAdmin(WorkspaceModelAdmin):
    list_display = ("user", "document", "version", "accepted_at", "ip_address")
    list_filter = ("document", "version")
    search_fields = ("user__email",)
    readonly_fields = [field.name for field in UserConsent._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(OtpVerification)
class OtpVerificationAdmin(WorkspaceModelAdmin):
    list_display = ("user", "purpose", "is_used", "attempts", "expires_at", "created_at")
    list_filter = ("purpose", "is_used")
    search_fields = ("user__email",)
    exclude = ("code_hash",)
    readonly_fields = [field.name for field in OtpVerification._meta.fields if field.name != "code_hash"]

    def has_add_permission(self, request):
        return False
