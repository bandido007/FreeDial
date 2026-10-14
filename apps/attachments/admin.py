"""Admin registrations for the attachments domain."""

from django.contrib import admin

from apps.attachments.models import Attachment, AttachmentDefinition


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "file_type",
        "purpose",
        "size",
        "is_public",
        "scan_status",
        "created_by",
        "created_at",
    )
    list_filter = ("file_type", "purpose", "is_public", "scan_status")
    search_fields = ("name", "storage_key", "created_by__email")
    readonly_fields = [field.name for field in Attachment._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(AttachmentDefinition)
class AttachmentDefinitionAdmin(admin.ModelAdmin):
    list_display = ("name_en", "code", "kind", "max_size_mb", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("name_en", "name_sw", "code")
