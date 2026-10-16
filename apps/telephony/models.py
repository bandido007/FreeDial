"""Models of the telephony app.

* :class:`SipAccount` — a SIP login, owned by AlgoDial (audited, soft delete).
* :class:`Trunk`, :class:`OutboundRoute` — M4b: carriers, and which trunks an outside number takes
  (:mod:`apps.telephony.trunks` writes their PJSIP objects).
* :class:`PsEndpoint`, :class:`PsAuth`, :class:`PsAor`, :class:`PsRegistration`,
  :class:`PsEndpointIdIp` — Asterisk's PJSIP realtime tables. Asterisk's own Alembic creates them (``asterisk_schema``);
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


class TrunkStyleChoices(models.TextChoices):
    IP = "ip", "IP (the carrier sends and takes calls by address)"
    REGISTRATION = "registration", "Registration (AlgoDial signs in to the carrier)"


class Trunk(BaseModel):
    """A carrier connection. Its Asterisk endpoint is ``trunk-<name>``; calls in arrive in [from-carrier]."""

    name = models.CharField(max_length=30, help_text="Lower-case letters, digits and -, e.g. vodacom")
    carrier_preset = models.CharField(
        max_length=40, blank=True, help_text="The preset its defaults came from"
    )
    style = models.CharField(max_length=20, choices=TrunkStyleChoices.choices, default=TrunkStyleChoices.IP)
    hosts = models.CharField(
        max_length=300, help_text="The carrier's addresses, IP[:port], comma-separated, e.g. 196.41.0.10:5060"
    )
    username = models.CharField(max_length=80, blank=True)
    password_encrypted = models.TextField(blank=True, help_text="Encrypted (apps.core.crypto)")
    codecs = models.CharField(max_length=100, default="alaw,ulaw")
    dtmf_mode = models.CharField(max_length=20, default="rfc4733")
    from_user = models.CharField(max_length=40, blank=True, help_text="The number the carrier knows us by")
    send_pai = models.BooleanField(default=False, help_text="Send P-Asserted-Identity with the caller ID")
    max_channels = models.PositiveIntegerField(default=0, help_text="Calls in and out at once (0 = no limit)")
    # Numbers in: how the carrier's country writes them, to make them E.164.
    country_code = models.CharField(max_length=4, default="255")
    national_prefix = models.CharField(max_length=4, default="0", blank=True)
    international_prefix = models.CharField(max_length=4, default="00", blank=True)
    national_length = models.PositiveSmallIntegerField(default=9, help_text="Digits after the country code")
    # Numbers out: from E.164 to what the carrier wants.
    strip_digits = models.PositiveSmallIntegerField(
        default=0, help_text='Characters taken off E.164 ("+255" is 4)'
    )
    prefix = models.CharField(max_length=10, blank=True, help_text='Then put in front, e.g. "0" for national')
    external_prefix = models.CharField(
        max_length=4, blank=True, help_text='What agents may dial first, e.g. "9"'
    )

    class Meta:
        db_table = "telephony_trunks"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name"], condition=Q(deleted=False), name="uniq_live_trunk")
        ]

    def __str__(self):
        return self.name

    @property
    def endpoint(self) -> str:
        return f"trunk-{self.name}"

    @property
    def is_live(self) -> bool:
        return self.active and not self.deleted


class OutboundRoute(BaseModel):
    """Which outside numbers may be dialled, and through which trunks, in order."""

    name = models.CharField(max_length=80)
    patterns = models.TextField(help_text="One E.164 prefix per line, e.g. +2557. Anything else is refused.")
    caller_id = models.CharField(
        max_length=40, blank=True, help_text="The number callees see (empty: the carrier's)"
    )
    required_permission = models.CharField(max_length=60, default="can_dial_external")
    trunks = models.ManyToManyField(Trunk, through="RouteTrunk", related_name="routes")

    class Meta:
        db_table = "telephony_outbound_routes"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def pattern_list(self) -> list[str]:
        return [line.strip() for line in self.patterns.splitlines() if line.strip()]


class RouteTrunk(models.Model):
    route = models.ForeignKey(OutboundRoute, on_delete=models.CASCADE, related_name="links")
    trunk = models.ForeignKey(Trunk, on_delete=models.CASCADE, related_name="route_links")
    position = models.PositiveSmallIntegerField(default=1, help_text="1 is tried first")

    class Meta:
        db_table = "telephony_route_trunks"
        ordering = ["position"]

    def __str__(self):
        return f"{self.position}. {self.trunk}"


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
    # M4b, trunks only
    dtmf_mode = models.CharField(max_length=20, null=True)
    from_user = models.CharField(max_length=40, null=True)
    send_pai = models.CharField(max_length=10, null=True)  # ast_bool_values
    outbound_auth = models.CharField(max_length=255, null=True)
    permit = models.CharField(max_length=95, null=True)
    deny = models.CharField(max_length=95, null=True)

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
    contact = models.CharField(max_length=2048, null=True)  # trunks: where the carrier is

    class Meta:
        managed = False
        db_table = "ps_aors"

    def __str__(self):
        return self.id


class PsRegistration(models.Model):
    """An outbound registration (a "registration" trunk signing in to its carrier)."""

    id = models.CharField(primary_key=True, max_length=255)
    server_uri = models.CharField(max_length=255, null=True)
    client_uri = models.CharField(max_length=255, null=True)
    outbound_auth = models.CharField(max_length=255, null=True)
    transport = models.CharField(max_length=40, null=True)
    endpoint = models.CharField(max_length=255, null=True)
    retry_interval = models.IntegerField(null=True)
    expiration = models.IntegerField(null=True)
    contact_user = models.CharField(max_length=40, null=True)

    class Meta:
        managed = False
        db_table = "ps_registrations"

    def __str__(self):
        return self.id


class PsEndpointIdIp(models.Model):
    """Calls from these addresses are this trunk's (res_pjsip_endpoint_identifier_ip)."""

    id = models.CharField(primary_key=True, max_length=255)
    endpoint = models.CharField(max_length=255, null=True)
    match = models.CharField(max_length=80, null=True)

    class Meta:
        managed = False
        db_table = "ps_endpoint_id_ips"

    def __str__(self):
        return self.id
