from django import forms
from django.contrib import admin, messages

from apps.core.exceptions import ApplicationError
from apps.rbac.admin_access import RbacAdminMixin
from apps.telephony import services
from apps.telephony.models import SipAccount
from apps.telephony.permissions import MANAGE_SIP_ACCOUNTS, VIEW_SIP_ACCOUNTS


class SipAccountForm(forms.ModelForm):
    new_password = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False),
        help_text=f"At least {services.MIN_PASSWORD_LENGTH} characters. Leave empty to generate one "
        "(new account) or keep the current one (existing account).",
    )

    class Meta:
        model = SipAccount
        fields = ("sip_id", "kind", "display_name", "codecs", "context", "active")
        help_texts = {
            "codecs": "Empty = the kind's default (ulaw,alaw for both: this Asterisk has no opus).",
            "context": "Empty = the default dialplan context.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:  # BROWSER accounts are made by AlgoDial at sign-in, never by hand
            self.fields["kind"].choices = [
                (value, label) for value, label in self.fields["kind"].choices if value != "BROWSER"
            ]
            self.fields["kind"].initial = "SOFTPHONE"
        self.fields["codecs"].required = False
        self.fields["context"].required = False

    def clean(self):
        data = super().clean()
        if "sip_id" in data and "kind" in data:
            try:
                services.validate_sip_account(
                    sip_id=data["sip_id"],
                    kind=data["kind"],
                    password=data.get("new_password"),
                    codecs=data.get("codecs"),
                    except_pk=self.instance.pk,
                )
            except ApplicationError as exc:
                raise forms.ValidationError(exc.message) from exc
        return data


@admin.register(SipAccount)
class SipAccountAdmin(RbacAdminMixin, admin.ModelAdmin):
    """Every write goes through the services, so the rules and the PJSIP rows stay one."""

    view_permission = VIEW_SIP_ACCOUNTS
    manage_permission = MANAGE_SIP_ACCOUNTS
    form = SipAccountForm
    list_display = ("sip_id", "kind", "display_name", "context", "active", "updated_at")
    list_filter = ("kind", "active")
    search_fields = ("sip_id", "display_name")

    def save_model(self, request, obj, form, change):
        data = form.cleaned_data
        password = data.get("new_password") or (None if change else services.generate_password())
        fields = {
            "sip_id": data["sip_id"],
            "kind": data["kind"],
            "password": password,
            "display_name": data["display_name"],
            "codecs": data["codecs"],
            "context": data["context"],
        }
        if change:
            account = services.update_sip_account(uid=obj.uid, actor=request.user, **fields)
        else:
            account = services.create_sip_account(actor=request.user, **fields)
        if account.active != data["active"]:
            account = services.set_sip_account_active(
                uid=account.uid, actor=request.user, active=data["active"]
            )
        obj.pk, obj.uid = account.pk, account.uid
        if password:
            messages.warning(
                request,
                f"SIP password for {account.sip_id}: {password}  (shown once: put it in the phone now)",
            )

    def delete_model(self, request, obj):
        services.delete_sip_account(uid=obj.uid)

    def delete_queryset(self, request, queryset):
        # The bulk action would bypass the soft delete; go one by one through the service.
        for account in queryset:
            services.delete_sip_account(uid=account.uid)
