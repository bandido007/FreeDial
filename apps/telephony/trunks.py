"""M4b: trunks (carriers) and outbound routes: their rules, and the PJSIP objects Asterisk reads.

A live trunk is, under the id ``trunk-<name>``:

- ``ps_endpoints``: context ``from-carrier``, the carrier's codecs and DTMF, and permit/deny so only
  the carrier's addresses may use it;
- ``ps_aors``: the carrier's address as a static contact, checked every 30 s (qualify: its health);
- ``ps_endpoint_id_ips``: calls from those addresses are this trunk's;
- ``ps_auths`` (with credentials) and ``ps_registrations`` (style "registration"): AlgoDial signs in.

Asterisk reads endpoints and identifies on every request, but schedules a trunk's qualify (its
health) and reads registrations only when its SIP modules reload, so a save asks it to (over ARI,
after the transaction).
Outbound authentication needs the plain password: it is in ``ps_auths`` (readable only by Asterisk's
role) and encrypted in ``Trunk``; never in git (rule 9).
"""

import ipaddress
import json
import logging
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import requests
from django.conf import settings
from django.db import transaction

from apps.core.crypto import decrypt_secret, encrypt_secret
from apps.core.exceptions import DuplicateError, NotFoundError, ValidationError
from apps.telephony.models import (
    OutboundRoute,
    PsAor,
    PsAuth,
    PsEndpoint,
    PsEndpointIdIp,
    PsRegistration,
    RouteTrunk,
    Trunk,
    TrunkStyleChoices,
)

logger = logging.getLogger(__name__)

TRUNK_PREFIX = "trunk-"  # endpoint ids; SIP account ids may not start with it
BAD_TRUNK = 10010
TRUNK_TAKEN = 10011
BAD_ROUTE = 10012
NOT_FOUND = 10013
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,29}$")
PATTERN = re.compile(r"^\+\d{1,14}$")
CODECS = {"alaw", "ulaw", "g722", "gsm"}  # what this Asterisk can carry (no codec_opus, no g729)
DTMF_MODES = {"rfc4733", "inband", "info", "auto"}
CARRIERS = Path(__file__).resolve().parent / "carriers"
QUALIFY_SECONDS = 30
FIELDS = (
    "style", "hosts", "username", "codecs", "dtmf_mode", "from_user", "send_pai", "max_channels",
    "country_code", "national_prefix", "international_prefix", "national_length",
    "strip_digits", "prefix", "external_prefix",
)  # fmt: skip


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    description: str
    fields: dict


@cache
def presets() -> tuple[Preset, ...]:
    """The carrier presets: data files in git (apps/telephony/carriers/*.json)."""
    found = []
    for path in sorted(CARRIERS.glob("*.json")):
        data = json.loads(path.read_text())
        found.append(Preset(data["key"], data["name"], data.get("description", ""), data.get("fields", {})))
    return tuple(found)


def get_preset(key: str) -> Preset:
    match = next((p for p in presets() if p.key == key), None)
    if match is None:
        raise ValidationError(f"There is no carrier preset {key!r}.", code=BAD_TRUNK)
    return match


def parse_hosts(hosts: str) -> list[tuple[str, int | None]]:
    """ "196.41.0.10:5060, 196.41.0.11" -> [(ip, port), ...]. IP addresses only: permit/deny need them."""
    parsed = []
    for item in (part.strip() for part in (hosts or "").split(",")):
        if not item:
            continue
        address, _, port = item.partition(":")
        try:
            ipaddress.IPv4Address(address)
        except ValueError as exc:
            raise ValidationError(
                f"{item}: give the carrier's IP address (IPv4), optionally with :port.", code=BAD_TRUNK
            ) from exc
        if port and not (port.isdigit() and 0 < int(port) < 65536):
            raise ValidationError(f"{item}: the port is 1 to 65535.", code=BAD_TRUNK)
        parsed.append((address, int(port) if port else None))
    if not parsed:
        raise ValidationError("A trunk needs at least one host: the carrier's IP address.", code=BAD_TRUNK)
    return parsed


def _validate(trunk: Trunk, password: str) -> None:
    if not NAME.match(trunk.name or ""):
        raise ValidationError(
            "A trunk name is 2 to 30 lower-case letters, digits or -, e.g. vodacom.", code=BAD_TRUNK
        )
    if trunk.style not in TrunkStyleChoices.values:
        raise ValidationError("Style is ip or registration.", code=BAD_TRUNK)
    parse_hosts(trunk.hosts)
    codecs = [codec.strip() for codec in (trunk.codecs or "").split(",") if codec.strip()]
    if not codecs or set(codecs) - CODECS:
        raise ValidationError(f"Each codec must be one of {', '.join(sorted(CODECS))}.", code=BAD_TRUNK)
    trunk.codecs = ",".join(codecs)
    if trunk.dtmf_mode not in DTMF_MODES:
        raise ValidationError(f"DTMF mode is one of {', '.join(sorted(DTMF_MODES))}.", code=BAD_TRUNK)
    if trunk.style == TrunkStyleChoices.REGISTRATION and not (trunk.username and password):
        raise ValidationError(
            "A registration trunk needs the carrier's username and password.", code=BAD_TRUNK
        )
    if not trunk.country_code.isdigit():
        raise ValidationError("The country code is digits only, e.g. 255.", code=BAD_TRUNK)
    taken = Trunk.objects.filter(name=trunk.name).exclude(pk=trunk.pk).exists()
    if taken:
        raise DuplicateError(f"A trunk named {trunk.name} already exists.", code=TRUNK_TAKEN)


@transaction.atomic
def save_trunk(*, actor, name, uid=None, preset=None, password=None, active=True, **fields) -> Trunk:
    """Create (no ``uid``) or change a trunk. ``preset`` fills what ``fields`` leave out (new trunks)."""
    if uid:
        trunk = Trunk.objects.filter(uid=uid).first()
        if trunk is None:
            raise NotFoundError("Trunk not found.", code=NOT_FOUND)
    else:
        trunk = Trunk(created_by=actor)
        if preset:
            trunk.carrier_preset = preset
            for field, value in get_preset(preset).fields.items():
                setattr(trunk, field, value)
    was_registration = bool(trunk.pk) and trunk.style == TrunkStyleChoices.REGISTRATION
    trunk.name = (name or "").strip().lower()
    for field in FIELDS:
        if field in fields and fields[field] is not None:
            setattr(trunk, field, fields[field].strip() if isinstance(fields[field], str) else fields[field])
    if password:
        trunk.password_encrypted = encrypt_secret(password)
    _validate(
        trunk, password or (decrypt_secret(trunk.password_encrypted) if trunk.password_encrypted else "")
    )
    trunk.active, trunk.updated_by = active, actor
    old_endpoint = (
        Trunk.all_objects.filter(pk=trunk.pk).values_list("name", flat=True).first() if trunk.pk else None
    )
    trunk.save()
    if old_endpoint and old_endpoint != trunk.name:
        remove(TRUNK_PREFIX + old_endpoint)
    sync(trunk)
    registrations = was_registration or trunk.style == TrunkStyleChoices.REGISTRATION
    transaction.on_commit(lambda: reload_asterisk(registrations=registrations))
    logger.info("[telephony] trunk %s saved by %s", trunk.name, getattr(actor, "pk", None))
    return trunk


@transaction.atomic
def delete_trunk(*, uid) -> None:
    trunk = Trunk.objects.filter(uid=uid).first()
    if trunk is None:
        raise NotFoundError("Trunk not found.", code=NOT_FOUND)
    registration = trunk.style == TrunkStyleChoices.REGISTRATION
    trunk.delete()
    remove(trunk.endpoint)
    transaction.on_commit(lambda: reload_asterisk(registrations=registration))


def sync(trunk: Trunk) -> None:
    if trunk.is_live:
        write(trunk)
    else:
        remove(trunk.endpoint)


def write(trunk: Trunk) -> None:
    endpoint = trunk.endpoint
    hosts = parse_hosts(trunk.hosts)
    password = decrypt_secret(trunk.password_encrypted) if trunk.password_encrypted else ""
    credentials = bool(trunk.username and password)
    PsEndpoint.objects.update_or_create(
        id=endpoint,
        defaults={
            "transport": settings.PJSIP_TRANSPORT_SOFTPHONE,
            "aors": endpoint,
            "auth": None,
            "outbound_auth": endpoint if credentials else None,
            "context": "from-carrier",
            "disallow": "all",
            "allow": trunk.codecs,
            "direct_media": "no",
            "dtmf_mode": trunk.dtmf_mode,
            "from_user": trunk.from_user or None,
            "send_pai": "yes" if trunk.send_pai else None,
            "callerid": None,
            "webrtc": None,
            "rewrite_contact": None,
            "force_rport": None,
            "deny": "0.0.0.0/0.0.0.0",
            "permit": ",".join(f"{address}/255.255.255.255" for address, _ in hosts),
        },
    )
    PsAor.objects.update_or_create(
        id=endpoint,
        defaults={
            "contact": ",".join(f"sip:{a}:{p}" if p else f"sip:{a}" for a, p in hosts),
            "qualify_frequency": QUALIFY_SECONDS,
            "max_contacts": None,
            "remove_existing": None,
        },
    )
    PsEndpointIdIp.objects.update_or_create(
        id=endpoint, defaults={"endpoint": endpoint, "match": ",".join(address for address, _ in hosts)}
    )
    if credentials:
        PsAuth.objects.update_or_create(
            id=endpoint,
            defaults={
                "auth_type": "userpass",
                "username": trunk.username,
                "password": password,
                "realm": None,
                "md5_cred": None,
                "password_digest": None,
            },
        )
    else:
        PsAuth.objects.filter(id=endpoint).delete()
    if trunk.style == TrunkStyleChoices.REGISTRATION:
        address, port = hosts[0]
        server = f"sip:{address}:{port}" if port else f"sip:{address}"
        PsRegistration.objects.update_or_create(
            id=endpoint,
            defaults={
                "server_uri": server,
                "client_uri": f"sip:{trunk.username}@{server.removeprefix('sip:')}",
                "outbound_auth": endpoint,
                "transport": settings.PJSIP_TRANSPORT_SOFTPHONE,
                "endpoint": endpoint,
                "retry_interval": 60,
                "expiration": 3600,
                "contact_user": trunk.username,
            },
        )
    else:
        PsRegistration.objects.filter(id=endpoint).delete()


def remove(endpoint: str) -> None:
    for model in (PsEndpoint, PsAor, PsAuth, PsEndpointIdIp, PsRegistration):
        model.objects.filter(id=endpoint).delete()


def reload_asterisk(registrations: bool = False) -> None:
    """Asterisk notices a new or changed trunk only when its SIP modules reload (ARI, no CLI needed).

    Measured on the lab (16 Oct 2026): a trunk added while Asterisk runs is never qualified until
    res_pjsip reloads; then it shows online within about 20 s. Outbound registrations are read when
    their own module reloads. Neither reload drops calls or phones' registrations."""
    modules = ["res_pjsip.so"] + (["res_pjsip_outbound_registration.so"] if registrations else [])
    for module in modules:
        try:
            response = requests.put(
                f"{settings.ARI_URL}/asterisk/modules/{module}",
                auth=(settings.ARI_USER, settings.ARI_PASSWORD),
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException:
            logger.warning("[telephony] could not ask Asterisk to reload %s", module, exc_info=True)


# ── outbound routes ─────────────────────────────────────────────────────────
def clean_patterns(patterns: str) -> str:
    """One E.164 prefix per line; anything else is refused with what was wrong."""
    lines = [line.strip() for line in (patterns or "").splitlines() if line.strip()]
    wrong = [line for line in lines if not PATTERN.match(line)]
    if not lines or wrong:
        raise ValidationError(
            "Each pattern is an E.164 prefix on its own line, e.g. +2557"
            + (f" (not {', '.join(wrong)})" if wrong else "")
            + ".",
            code=BAD_ROUTE,
        )
    return "\n".join(lines)


@transaction.atomic
def save_route(
    *,
    actor,
    name,
    patterns,
    trunks,
    uid=None,
    caller_id="",
    required_permission="can_dial_external",
    active=True,
) -> OutboundRoute:
    """Create or change a route. ``trunks``: in the order they are tried."""
    lines = clean_patterns(patterns).splitlines()
    if not trunks:
        raise ValidationError("A route needs at least one trunk.", code=BAD_ROUTE)
    if not (name or "").strip():
        raise ValidationError("A route needs a name.", code=BAD_ROUTE)
    route = OutboundRoute.objects.filter(uid=uid).first() if uid else OutboundRoute(created_by=actor)
    if route is None:
        raise NotFoundError("Route not found.", code=NOT_FOUND)
    route.name, route.patterns, route.caller_id = name.strip(), "\n".join(lines), (caller_id or "").strip()
    route.required_permission, route.active, route.updated_by = required_permission, active, actor
    route.save()
    route.links.all().delete()
    RouteTrunk.objects.bulk_create(
        [RouteTrunk(route=route, trunk=trunk, position=n) for n, trunk in enumerate(trunks, start=1)]
    )
    return route
