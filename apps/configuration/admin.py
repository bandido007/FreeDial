from django.contrib import admin

from apps.configuration.models import SystemSetting


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "value", "updated_by", "updated_at")
    search_fields = ("key",)
    readonly_fields = ("key",)

    def has_add_permission(self, request):
        return False
