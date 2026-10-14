"""Models of the telephony app.

* :class:`SipAccount` — a SIP login, owned by AlgoDial (audited, soft delete).
* :class:`PsEndpoint`, :class:`PsAuth`, :class:`PsAor` — Asterisk's PJSIP
  realtime tables. Asterisk's own Alembic creates them (``asterisk_schema``);
  Django only maps the columns AlgoDial writes and never migrates them.
  :mod:`apps.telephony.pjsip` keeps them in step with the accounts.
"""

from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel
from apps.telephony.enums import SipAccountKindChoices


class SipAccount(BaseModel):
    """One SIP login. Its id is the PJSIP endpoint, auth and aor id, and the SIP username."""

    AUDIT_MASK_FIELDS = ("password_encrypted",)

    sip_id = models.CharField(max_length=40, help_text="Endpoint id and SIP username, e.g. 1003")
    kind = models.CharField(max_length=20, choices=SipAccountKindChoices.choices())
    display_name = models.CharField(max_length=30, blank=True, help_text="Caller ID name")
    password_encrypted = models.TextField(help_text="The SIP password, encrypted (apps.core.crypto)")
    codecs = models.CharField(max_length=200, help_text="Allowed codecs in order, e.g. ulaw,alaw")
    context = models.CharField(max_length=40, help_text="Dialplan context for calls from this account")

    class Meta:
        db_table = "telephony_sip_accounts"
        ordering = ["sip_id"]
        verbose_name = "SIP account"
        verbose_name_plural = "SIP accounts"
        constraints = [
            models.UniqueConstraint(fields=["sip_id"], condition=Q(deleted=False), name="uniq_live_sip_id"),
        ]

    def __str__(self):
        return self.sip_id

    @property
    def is_live(self) -> bool:
        """Asterisk should see this account: active and not deleted."""
        return self.active and not self.deleted


class PsEndpoint(models.Model):
    id = models.CharField(primary_key=True, max_length=255)
    transport = models.CharField(max_length=40, null=True)
    aors = models.CharField(max_length=2048, null=True)
    auth = models.CharField(max_length=255, null=True)
    context = models.CharField(max_length=40, null=True)
    disallow = models.CharField(max_length=200, null=True)
    allow = models.CharField(max_length=200, null=True)
    direct_media = models.CharField(max_length=10, null=True)  # ast_bool_values
    callerid = models.CharField(max_length=40, null=True)
    webrtc = models.CharField(max_length=10, null=True)  # ast_bool_values
    rewrite_contact = models.CharField(max_length=10, null=True)  # ast_bool_values
    force_rport = models.CharField(max_length=10, null=True)  # ast_bool_values

    class Meta:
        managed = False
        db_table = "ps_endpoints"

    def __str__(self):
        return self.id


class PsAuth(models.Model):
    id = models.CharField(primary_key=True, max_length=255)
    auth_type = models.CharField(max_length=20, null=True)
    username = models.CharField(max_length=40, null=True)
    realm = models.CharField(max_length=255, null=True)
    password = models.CharField(max_length=80, null=True)
    md5_cred = models.CharField(max_length=40, null=True)
    password_digest = models.CharField(max_length=1024, null=True)

    class Meta:
        managed = False
        db_table = "ps_auths"

    def __str__(self):
        return self.id


class PsAor(models.Model):
    id = models.CharField(primary_key=True, max_length=255)
    max_contacts = models.IntegerField(null=True)
    remove_existing = models.CharField(max_length=10, null=True)  # ast_bool_values
    qualify_frequency = models.IntegerField(null=True)

    class Meta:
        managed = False
        db_table = "ps_aors"

    def __str__(self):
        return self.id
