"""Models of the users domain.

* :class:`User` — the concrete account. **This is the class a project
  extends**: add your own fields below the marker. The fields every system
  needs come from :class:`~apps.users.abstract.AbstractStarterUser`.
* :class:`OtpVerification` — one-time codes that prove control of a contact.
* :class:`UserConsent` — the auditable record of each legal-document acceptance.
"""

from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel
from apps.users.abstract import AbstractStarterUser
from apps.users.enums import ConsentDocumentChoices


class User(AbstractStarterUser):
    """Authenticated account, identified by a unique email address."""

    # ── Project fields ───────────────────────────────────────────────────────
    # Add the fields your system needs here (then `make migrations`). Expose
    # them on `apps.users.types.UserType` if the API should return them.

    class Meta(AbstractStarterUser.Meta):
        abstract = False
        db_table = "users"
        verbose_name = "User"
        verbose_name_plural = "Users"


class OtpVerification(BaseModel):
    """A one-time verification code issued to a user.

    The code itself is **never stored** — only a salted hash (Django
    ``make_password``/``check_password``, PBKDF2 with a per-row salt), so a
    database leak does not reveal live codes. ``channels_sent`` records which
    channels the code was actually delivered on (e.g.
    ``{"email": true, "sms": false}``).
    """

    # Short-lived secret carrier: nothing here belongs in the change trail.
    AUDIT_EXCLUDE = True

    user = models.ForeignKey(User, related_name="otp_verifications", on_delete=models.CASCADE)
    code_hash = models.CharField(max_length=255, help_text="Salted hash of the code (never the code).")
    purpose = models.CharField(max_length=40, default="registration", db_index=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveIntegerField(default=0)
    is_used = models.BooleanField(default=False)
    channels_sent = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "otp_verifications"
        ordering = ["-created_at"]
        verbose_name = "OTP verification"
        verbose_name_plural = "OTP verifications"
        indexes = [models.Index(fields=["user", "purpose", "is_used"])]

    def __str__(self):
        return f"OTP[{self.purpose}] {self.user} ({'used' if self.is_used else 'active'})"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at


class UserConsent(BaseModel):
    """An auditable record of one legal-document acceptance.

    One row per (document, version) a user accepts — never overwritten — so the
    system can prove *when* and *from where* lawful basis was obtained, and ask
    again when the current published version (``settings.CONSENT_*_VERSION``)
    moves past the accepted one.
    """

    user = models.ForeignKey(User, related_name="consents", on_delete=models.CASCADE)
    document = models.CharField(max_length=20, choices=ConsentDocumentChoices.choices())
    version = models.CharField(max_length=40, help_text="Policy version accepted, e.g. '2026-01'")
    accepted_at = models.DateTimeField(default=timezone.now)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        db_table = "user_consents"
        ordering = ["-accepted_at"]
        verbose_name = "User consent"
        verbose_name_plural = "User consents"
        indexes = [models.Index(fields=["user", "document"])]
        constraints = [
            models.UniqueConstraint(fields=["user", "document", "version"], name="uniq_user_consent_version")
        ]

    def __str__(self):
        return f"{self.user} · {self.document} v{self.version}"
