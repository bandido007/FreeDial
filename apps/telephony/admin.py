from django import forms
from django.contrib import admin, messages
from django.core.cache import cache

from apps.core.exceptions import ApplicationError
from apps.rbac.admin_access import RbacAdminMixin
from apps.telephony import health, services, trunks
from apps.telephony.models import OutboundRoute, RouteTrunk, SipAccount, Trunk
from apps.telephony.permissions import MANAGE_SIP_ACCOUNTS, MANAGE_TRUNKS, VIEW_SIP_ACCOUNTS, VIEW_TRUNKS


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


# ── M4b: trunks and outbound routes ─────────────────────────────────────────
class TrunkForm(forms.ModelForm):
    new_password = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="The carrier's password, for registration trunks. Leave empty to keep the current one.",
    )
    preset = forms.ChoiceField(
        required=False,
        help_text="A new trunk starts from these defaults; what you fill in below wins.",
    )

    class Meta:
        model = Trunk
        fields = (
            "name", "style", "hosts", "username", "codecs", "dtmf_mode", "from_user", "send_pai",
            "max_channels", "country_code", "national_prefix", "international_prefix", "national_length",
            "strip_digits", "prefix", "external_prefix", "active",
        )  # fmt: skip

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["preset"].choices = [("", "None")] + [(p.key, p.name) for p in trunks.presets()]
        if self.instance.pk:
            del self.fields["preset"]


@admin.register(Trunk)
class TrunkAdmin(RbacAdminMixin, admin.ModelAdmin):
    """Every save goes through apps.telephony.trunks, so the rules and Asterisk's rows stay one."""

    view_permission = VIEW_TRUNKS
    manage_permission = MANAGE_TRUNKS
    form = TrunkForm
    list_display = ("name", "style", "hosts", "health", "max_channels", "active", "updated_at")
    search_fields = ("name", "hosts")

    @admin.display(description="Health")
    def health(self, obj):
        """Reachable (Asterisk's 30-second check, over ARI) and, for sign-in trunks, registered (AMI)."""
        if not obj.is_live:
            return "Switched off"
        registration = None
        if obj.style == "registration":
            found = cache.get_or_set("telephony.trunk-registrations", health.registrations, 10)
            registration = "unknown" if found is None else found.get(obj.endpoint, "Unregistered")
        return health.TrunkHealth(health.reachable(obj.endpoint), registration).label

    def save_model(self, request, obj, form, change):
        data = dict(form.cleaned_data)
        password = data.pop("new_password", "") or None
        chosen = data.pop("preset", "") or None
        name, active = data.pop("name"), data.pop("active")
        try:
            trunk = trunks.save_trunk(
                actor=request.user,
                uid=obj.uid if change else None,
                name=name,
                preset=chosen,
                password=password,
                active=active,
                **data,
            )
        except ApplicationError as exc:
            messages.error(request, exc.message)
            raise
        obj.pk, obj.uid = trunk.pk, trunk.uid

    def delete_model(self, request, obj):
        trunks.delete_trunk(uid=obj.uid)

    def delete_queryset(self, request, queryset):
        for trunk in queryset:
            trunks.delete_trunk(uid=trunk.uid)


class RouteForm(forms.ModelForm):
    class Meta:
        model = OutboundRoute
        fields = ("name", "patterns", "caller_id", "required_permission", "active")

    def clean_patterns(self):
        try:
            return trunks.clean_patterns(self.cleaned_data.get("patterns"))
        except ApplicationError as exc:
            raise forms.ValidationError(exc.message) from exc


class AtLeastOneTrunk(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        kept = [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        if not kept:
            raise forms.ValidationError("A route needs at least one trunk.")


class RouteTrunkInline(RbacAdminMixin, admin.TabularInline):
    view_permission = VIEW_TRUNKS
    manage_permission = MANAGE_TRUNKS
    model = RouteTrunk
    formset = AtLeastOneTrunk
    extra = 1
    verbose_name = "trunk"
    verbose_name_plural = "Trunks, in the order they are tried (1 first)"

    def has_add_permission(self, request, obj=None):  # an inline's version takes obj
        return self._holds(request, self.manage_permission)


@admin.register(OutboundRoute)
class OutboundRouteAdmin(RbacAdminMixin, admin.ModelAdmin):
    view_permission = VIEW_TRUNKS
    manage_permission = MANAGE_TRUNKS
    form = RouteForm
    inlines = [RouteTrunkInline]
    list_display = ("name", "patterns", "caller_id", "active")
