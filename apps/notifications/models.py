"""Notification domain: the delivery log, its channel configuration, and what
each person chose to receive.

* **The log/outbox** — :class:`Notification`: one row per message on one channel
  (EMAIL / SMS / INAPP). This is the auditable overview: what was sent, to
  whom, on which channel, its status, attempts and remarks, and the source of
  the *resend* feature. In-app is just ``channel=INAPP`` with ``is_read``.
* **Channel configuration** — :class:`EmailConfiguration` (SMTP) and
  :class:`SmsConfiguration` (the gateway), editable at runtime so credentials
  change without a redeploy. Secrets (SMTP password, SMS API key) are stored
  encrypted (see ``apps.core.crypto``).
* **Content configuration** — :class:`NotificationTemplate`: a per
  (category, channel) subject/body so notification copy is configurable.
* **Preferences** — :class:`NotificationPreference`: a person's opt-out of one
  category on one channel.

``category`` is a plain string on purpose: categories are declared in code by
the app that sends them (see ``catalogue.py``), so a new one needs no migration.
"""

from django.conf import settings
from django.db import models

from apps.core.crypto import decrypt_secret, encrypt_secret
from apps.core.models import BaseModel
from apps.notifications.enums import NotificationChannelChoices, NotificationStatusChoices


class Notification(BaseModel):
    """A single message on a single channel — the unit of the delivery log.

    The same row models an e-mail, an SMS or an in-app item (by ``channel``).
    ``status`` / ``is_sent`` / ``attempts`` / ``remarks`` track delivery so the
    admin overview and the resend/retry machinery have everything they need.
    """

    # The log is system-written and high-volume: keep it out of the change trail.
    AUDIT_EXCLUDE = True

    channel = models.CharField(
        max_length=10,
        choices=NotificationChannelChoices.choices(),
        db_index=True,
        help_text="Delivery channel: EMAIL / SMS / INAPP.",
    )
    category = models.CharField(
        max_length=60, blank=True, db_index=True, help_text="Kind of message (drives template lookup)."
    )
    status = models.CharField(
        max_length=10,
        choices=NotificationStatusChoices.choices(),
        default=NotificationStatusChoices.PENDING.value,
        db_index=True,
    )

    # Content.
    title = models.CharField(max_length=255, blank=True)
    subject = models.CharField(max_length=255, blank=True)
    salutation = models.CharField(max_length=255, blank=True)
    message = models.TextField(blank=True)
    html_template = models.CharField(max_length=255, blank=True, help_text="Template path for HTML email.")
    variables = models.JSONField(default=dict, blank=True, help_text="Render context for the template.")
    url = models.CharField(max_length=500, blank=True)

    # Recipient (denormalised so the log stays readable even if the user changes).
    receiver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="notifications",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    email_to = models.CharField(max_length=255, blank=True)
    phone_to = models.CharField(max_length=20, blank=True)
    full_name = models.CharField(max_length=255, blank=True)

    # Delivery bookkeeping.
    attempts = models.PositiveIntegerField(default=0)
    remarks = models.TextField(blank=True, help_text="Last error / delivery note.")
    sent_time = models.DateTimeField(null=True, blank=True)
    is_sent = models.BooleanField(default=False)
    is_read = models.BooleanField(default=False, help_text="In-app: whether the receiver has read it.")
    is_redacted = models.BooleanField(
        default=False, help_text="The content carried a secret and was scrubbed after sending."
    )

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at"]
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"
        indexes = [
            models.Index(fields=["status", "channel"]),
            models.Index(fields=["receiver", "channel", "is_read"]),
        ]

    def __str__(self):
        return f"{self.channel} → {self.email_to or self.phone_to or self.full_name} [{self.status}]"


class EmailConfiguration(BaseModel):
    """Runtime-editable SMTP connection settings for the e-mail channel.

    The active/default row drives :mod:`apps.notifications.backends.email`
    (host/port/credentials/TLS). The password is encrypted at rest and exposed
    through :meth:`set_password` / :meth:`get_password` only.
    """

    AUDIT_MASK_FIELDS = ("password_encrypted",)

    name = models.CharField(max_length=255, help_text="Label, e.g. 'Primary SMTP'.")
    host = models.CharField(max_length=255)
    port = models.PositiveIntegerField(default=587)
    username = models.CharField(max_length=255, blank=True)
    password_encrypted = models.CharField(max_length=1024, blank=True)
    use_tls = models.BooleanField(default=True)
    use_ssl = models.BooleanField(default=False)
    verify_tls = models.BooleanField(
        default=True,
        help_text="Verify the server certificate. Un-tick only for an internal relay "
        "with a self-signed certificate.",
    )
    from_email = models.EmailField(blank=True, help_text="Default From address.")
    from_name = models.CharField(max_length=255, blank=True)
    is_default = models.BooleanField(default=False, help_text="The configuration the e-mail channel uses.")

    class Meta:
        db_table = "email_configurations"
        ordering = ["-created_at"]
        verbose_name = "Email configuration"
        verbose_name_plural = "Email configurations"
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=models.Q(is_default=True, deleted=False),
                name="unique_default_email_configuration",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.host}:{self.port})"

    def set_password(self, raw: str | None) -> None:
        self.password_encrypted = encrypt_secret(raw)

    def get_password(self) -> str:
        return decrypt_secret(self.password_encrypted)

    @property
    def sender(self) -> str:
        addr = self.from_email or self.username
        return f"{self.from_name} <{addr}>" if self.from_name and addr else addr


class SmsConfiguration(BaseModel):
    """Runtime-editable SMS gateway settings.

    **Every gateway field is optional here**: a blank value falls back to the
    matching ``SMS_*`` setting from the environment (see
    ``backends/sms.py:resolve_gateway``), so ops may run entirely from the
    environment and use this row only to override or to switch provider.

    ``provider`` is validated against the provider registry, not a fixed list,
    so a project can add a gateway without a migration.
    """

    AUDIT_MASK_FIELDS = ("api_key_encrypted",)

    name = models.CharField(max_length=255, help_text="Label, e.g. 'Primary gateway'.")
    provider = models.CharField(
        max_length=20,
        default="LOG",
        help_text="Which delivery implementation this row drives (see backends/sms.py).",
    )
    api_url = models.CharField(
        max_length=512, blank=True, help_text="Gateway endpoint (SMS_API_URL); blank = environment."
    )
    sender_id = models.CharField(
        max_length=50, blank=True, help_text="Registered sender ID (SMS_SENDER_ID); blank = environment."
    )
    system_id = models.CharField(
        max_length=100, blank=True, help_text="System ID (SMS_SYSTEM_ID); blank = environment."
    )
    service_code = models.CharField(
        max_length=100, blank=True, help_text="Service code (SMS_SERVICE_CODE); blank = environment."
    )
    username = models.CharField(max_length=255, blank=True, help_text="Gateway username, if required.")
    api_key_encrypted = models.CharField(max_length=1024, blank=True)
    is_default = models.BooleanField(default=False, help_text="The configuration the SMS channel uses.")

    class Meta:
        db_table = "sms_configurations"
        ordering = ["-created_at"]
        verbose_name = "SMS configuration"
        verbose_name_plural = "SMS configurations"
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=models.Q(is_default=True, deleted=False),
                name="unique_default_sms_configuration",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.provider})"

    def set_api_key(self, raw: str | None) -> None:
        self.api_key_encrypted = encrypt_secret(raw)

    def get_api_key(self) -> str:
        return decrypt_secret(self.api_key_encrypted)


class NotificationTemplate(BaseModel):
    """Configurable subject/body for a (category, channel) pair.

    Lets an administrator edit notification copy without code changes. ``body``
    may contain ``{placeholder}`` tokens filled from a notification's
    ``variables``.
    """

    name = models.CharField(max_length=255)
    category = models.CharField(max_length=60, db_index=True)
    channel = models.CharField(max_length=10, choices=NotificationChannelChoices.choices())
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "notification_templates"
        ordering = ["category", "channel"]
        verbose_name = "Notification template"
        verbose_name_plural = "Notification templates"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "channel"],
                condition=models.Q(is_active=True, deleted=False),
                name="unique_active_template_per_category_channel",
            )
        ]

    def __str__(self):
        return f"{self.name} · {self.category}/{self.channel}"


class NotificationPreference(BaseModel):
    """One person's choice for one category on one channel.

    Only opt-outs need a row: no row means "send". Mandatory categories
    (security and account notices) ignore preferences altogether.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="notification_preferences", on_delete=models.CASCADE
    )
    category = models.CharField(max_length=60)
    channel = models.CharField(max_length=10, choices=NotificationChannelChoices.choices())
    enabled = models.BooleanField(default=True)

    class Meta:
        db_table = "notification_preferences"
        ordering = ["category", "channel"]
        verbose_name = "Notification preference"
        verbose_name_plural = "Notification preferences"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "category", "channel"],
                condition=models.Q(deleted=False),
                name="unique_notification_preference",
            )
        ]

    def __str__(self):
        state = "on" if self.enabled else "off"
        return f"{self.user} · {self.category}/{self.channel} {state}"
