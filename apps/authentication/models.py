"""Models of the authentication domain.

=======================  =====================================================
Model                    What it holds
=======================  =====================================================
AuthenticationAudit      every security-relevant event (append-only)
UserLoginHistory         every successful sign-in, with the device
PasswordResetToken       single-use emailed links (reset, invitation)
PasswordHistory          hashes of recent passwords (reuse prevention)
MfaCredential            a person's authenticator secret
MfaRecoveryCode          single-use fallback codes
ExternalIdentity         the link between an account and an external provider
LdapConfig               a directory that accounts sign in against
=======================  =====================================================

The two logs are plain ``models.Model`` on purpose: they cannot be soft-deleted,
and they never enter the change trail (which would record the log of a log).
"""

import hashlib

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.utils import timezone
from user_agents import parse as parse_user_agent

from apps.authentication.enums import AuthEventChoices, MfaMethodChoices, TokenPurposeChoices
from apps.core.crypto import decrypt_secret, encrypt_secret
from apps.core.models import BaseModel
from apps.core.utils import generate_uuid7, get_client_ip

USER = settings.AUTH_USER_MODEL


class AuthenticationAudit(models.Model):
    """Every security-relevant event: sign-ins, failures, password and
    two-factor changes, revoked sessions."""

    uid = models.UUIDField(default=generate_uuid7, unique=True, editable=False, db_index=True)
    user = models.ForeignKey(
        USER, related_name="auth_audits", null=True, blank=True, on_delete=models.SET_NULL
    )
    email = models.CharField(max_length=255, blank=True, db_index=True)
    event_type = models.CharField(max_length=40, choices=AuthEventChoices.choices(), db_index=True)
    status = models.CharField(max_length=10, db_index=True)  # "success" | "failure"
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "authentication_audits"
        ordering = ["-created_at"]
        verbose_name_plural = "Authentication audits"

    def __str__(self):
        return f"{self.email or 'unknown'} · {self.event_type} · {self.status}"


class UserLoginHistory(models.Model):
    """Append-only record of successful sign-ins (device / location forensics)."""

    user = models.ForeignKey(USER, related_name="login_history", on_delete=models.CASCADE)
    logged_in_at = models.DateTimeField(default=timezone.now, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    browser = models.CharField(max_length=255, blank=True)
    os = models.CharField(max_length=255, blank=True)
    device = models.CharField(max_length=255, blank=True)
    provider = models.CharField(max_length=40, blank=True, help_text="How the person signed in.")
    fingerprint = models.CharField(
        max_length=64, blank=True, db_index=True, help_text="Hash of browser + OS + device families."
    )
    is_new_device = models.BooleanField(default=False)

    class Meta:
        db_table = "user_login_history"
        ordering = ["-logged_in_at"]
        verbose_name_plural = "User login history"
        indexes = [models.Index(fields=["user", "fingerprint"])]

    def __str__(self):
        return f"{self.user} @ {self.logged_in_at:%Y-%m-%d %H:%M}"

    @property
    def label(self) -> str:
        """`Chrome 128 · Windows 10` — or 'Unknown device' when nothing was recorded."""
        parts = [part for part in (self.browser, self.os) if part]
        return " · ".join(parts) if parts else "Unknown device"

    @staticmethod
    def describe(user_agent: str) -> dict:
        """Browser, OS, device and a stable fingerprint from a user-agent string.

        The fingerprint uses the *families* only (Chrome, Windows, PC), so a
        browser update is not mistaken for a new device.
        """
        agent = parse_user_agent(user_agent or "")
        families = "|".join(
            [agent.browser.family or "", agent.os.family or "", agent.device.family or ""]
        ).lower()
        return {
            "browser": f"{agent.browser.family} {agent.browser.version_string}".strip(),
            "os": f"{agent.os.family} {agent.os.version_string}".strip(),
            "device": agent.device.family or "Unknown",
            "fingerprint": hashlib.sha256(families.encode()).hexdigest(),
        }

    @classmethod
    def record(cls, user, request, *, provider: str = "") -> "UserLoginHistory":
        user_agent = request.META.get("HTTP_USER_AGENT", "") if request is not None else ""
        details = cls.describe(user_agent)
        seen_before = cls.objects.filter(user=user, fingerprint=details["fingerprint"]).exists()
        first_login = not cls.objects.filter(user=user).exists()
        return cls.objects.create(
            user=user,
            ip_address=get_client_ip(request) or None,
            user_agent=user_agent,
            provider=provider or "",
            # The very first sign-in is not "a new device": there is nothing
            # to compare it with, and alerting on it would be noise.
            is_new_device=not seen_before and not first_login,
            **details,
        )


class PasswordResetToken(BaseModel):
    """A single-use, expiring token behind an emailed link.

    Only the **SHA-256 digest** of the token is stored — never the raw value
    that goes in the link — so a database leak cannot be turned into working
    links. Lookups hash the incoming token and match on the indexed
    ``token_hash``. A deterministic hash (not ``make_password``'s per-row salt)
    is used precisely *because* the token must be found by its digest; the
    token itself carries 384 bits of entropy.
    """

    AUDIT_EXCLUDE = True

    user = models.ForeignKey(USER, related_name="password_reset_tokens", on_delete=models.CASCADE)
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="SHA-256 hex digest of the token (never the raw token).",
    )
    purpose = models.CharField(
        max_length=10,
        choices=TokenPurposeChoices.choices(),
        default=TokenPurposeChoices.RESET.value,
    )
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    requested_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = "password_reset_tokens"
        ordering = ["-created_at"]
        verbose_name = "Password reset token"
        verbose_name_plural = "Password reset tokens"
        indexes = [models.Index(fields=["token_hash", "is_used"])]

    def __str__(self):
        return f"{self.purpose} token for {self.user} ({'used' if self.is_used else 'active'})"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @staticmethod
    def hash_token(raw: str) -> str:
        """Deterministic digest used both to store and to look a token up."""
        return hashlib.sha256((raw or "").encode()).hexdigest()


class PasswordHistory(models.Model):
    """The hash of a password an account used, kept to refuse its reuse."""

    user = models.ForeignKey(USER, related_name="password_history", on_delete=models.CASCADE)
    password_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "password_history"
        ordering = ["-created_at"]
        verbose_name_plural = "Password history"

    def __str__(self):
        return f"{self.user} @ {self.created_at:%Y-%m-%d}"


class MfaCredential(BaseModel):
    """A person's second factor. The secret is encrypted at rest.

    A row exists from the moment enrolment *starts*; it counts only once
    ``confirmed_at`` is set (the first code was entered correctly).
    ``last_used_step`` is the replay guard — see :mod:`apps.authentication.totp`.
    """

    AUDIT_MASK_FIELDS = ("secret_encrypted",)

    user = models.OneToOneField(USER, related_name="mfa", on_delete=models.CASCADE)
    method = models.CharField(
        max_length=10, choices=MfaMethodChoices.choices(), default=MfaMethodChoices.TOTP.value
    )
    secret_encrypted = models.CharField(max_length=512, blank=True, default="")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_step = models.BigIntegerField(default=0)

    class Meta:
        db_table = "mfa_credentials"
        verbose_name = "Two-factor credential"
        verbose_name_plural = "Two-factor credentials"

    def __str__(self):
        return f"{self.user} · {self.method} ({'active' if self.confirmed_at else 'pending'})"

    @property
    def secret(self) -> str:
        return decrypt_secret(self.secret_encrypted) if self.secret_encrypted else ""

    @secret.setter
    def secret(self, raw: str):
        self.secret_encrypted = encrypt_secret(raw) if raw else ""

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None


class MfaRecoveryCode(models.Model):
    """A single-use fallback code, stored as a hash. Used once, kept for audit."""

    user = models.ForeignKey(USER, related_name="mfa_recovery_codes", on_delete=models.CASCADE)
    code_hash = models.CharField(max_length=64, db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "mfa_recovery_codes"
        ordering = ["id"]
        verbose_name = "Two-factor recovery code"
        verbose_name_plural = "Two-factor recovery codes"

    def __str__(self):
        return f"{self.user} · {'used' if self.used_at else 'unused'}"


class ExternalIdentity(BaseModel):
    """The link between an account and an identity at an external provider.

    An external sign-in is matched on the provider's **subject** (``sub``), the
    one identifier a provider guarantees never changes or gets reassigned — an
    e-mail address can do both.
    """

    user = models.ForeignKey(USER, related_name="external_identities", on_delete=models.CASCADE)
    provider = models.CharField(max_length=40, db_index=True, help_text="Key from OIDC_PROVIDERS.")
    subject = models.CharField(max_length=255, help_text="The provider's stable identifier (sub).")
    email = models.CharField(max_length=255, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "external_identities"
        ordering = ["-created_at"]
        verbose_name = "External identity"
        verbose_name_plural = "External identities"
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "subject"],
                condition=models.Q(deleted=False),
                name="unique_external_identity",
            )
        ]

    def __str__(self):
        return f"{self.user} · {self.provider}"


def validate_ldap_url(value: str) -> None:
    """An LDAP URL uses the ldap:// or ldaps:// scheme."""
    if value and not value.startswith(("ldap://", "ldaps://")):
        raise DjangoValidationError("LDAP URL must start with ldap:// or ldaps://")


class LdapConfig(BaseModel):
    """A directory that accounts sign in against.

    ``email_domain`` decides who belongs to it: a person whose address ends in
    that domain is authenticated against this directory. The bind password is
    stored encrypted and only decrypted at bind time.
    """

    AUDIT_MASK_FIELDS = ("bind_password_encrypted",)

    name = models.CharField(max_length=255, blank=True, help_text="Label for this directory.")
    ldap_url = models.CharField(max_length=255, validators=[validate_ldap_url])
    bind_dn = models.CharField(max_length=255, help_text="Service-account DN used to search.")
    bind_password_encrypted = models.CharField(max_length=1024, blank=True)
    search_base = models.CharField(max_length=255, blank=True, help_text="Base DN for user search.")
    email_attribute = models.CharField(
        max_length=64, default="mail", help_text="Directory attribute holding the e-mail."
    )
    email_domain = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        help_text="e.g. example.org — addresses in this domain sign in against this directory.",
    )
    default_role = models.ForeignKey(
        "rbac.Role",
        related_name="ldap_configs",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Role granted to accounts that come from this directory.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "ldap_configs"
        ordering = ["-created_at"]
        verbose_name = "LDAP configuration"
        verbose_name_plural = "LDAP configurations"

    def __str__(self):
        return self.name or self.ldap_url

    def set_bind_password(self, raw: str | None) -> None:
        self.bind_password_encrypted = encrypt_secret(raw)

    def get_bind_password(self) -> str:
        return decrypt_secret(self.bind_password_encrypted)
