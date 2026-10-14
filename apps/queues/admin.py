from django import forms
from django.contrib import admin

from apps.core.exceptions import ApplicationError
from apps.queues import services
from apps.queues.models import EntryPoint, Queue, QueueMember
from apps.queues.permissions import EDIT_QUEUES, VIEW_QUEUES
from apps.rbac.admin_access import RbacAdminMixin


class QueueForm(forms.ModelForm):
    class Meta:
        model = Queue
        fields = ("name", "ring_timeout", "max_wait", "retry_delay", "wrapup_seconds", "moh_class", "active")

    def clean(self):
        data = super().clean()
        try:
            services.validate_queue(
                name=data.get("name"),
                except_pk=self.instance.pk,
                **{field: data.get(field) for field in services.LIMITS},
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
    list_display = ("name", "ring_timeout", "max_wait", "retry_delay", "wrapup_seconds", "active")
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
