"""Keeps Asterisk's PJSIP realtime rows in step with the SIP accounts.

An account that is active and not deleted has exactly one endpoint, one auth
and one aor row, all under its ``sip_id``; any other account has none. Asterisk
reads these tables on every request (no cache), so a change takes effect without
a reload. The auth row carries only an MD5 digest of ``user:realm:password``,
never the password itself.

Called from the model signals (``receivers``), so every way of saving or
deleting an account goes through here, inside the same database transaction.
"""

import hashlib

from django.conf import settings

from apps.core.crypto import decrypt_secret
from apps.telephony.enums import SipAccountKindChoices
from apps.telephony.models import PsAor, PsAuth, PsEndpoint, SipAccount


def password_digest(username: str, realm: str, password: str) -> str:
    """The ``password_digest`` value Asterisk expects: ``MD5:<md5 of user:realm:password>``."""
    # MD5 is what SIP digest authentication itself uses; this is the protocol's HA1, not our hashing choice.
    return "MD5:" + hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()  # noqa: S324


def write(account: SipAccount) -> None:
    browser = account.kind == SipAccountKindChoices.BROWSER.value
    callerid = f'"{account.display_name}" <{account.sip_id}>' if account.display_name else None
    PsEndpoint.objects.update_or_create(
        id=account.sip_id,
        defaults={
            "transport": settings.PJSIP_TRANSPORT_BROWSER if browser else settings.PJSIP_TRANSPORT_SOFTPHONE,
            "aors": account.sip_id,
            "auth": account.sip_id,
            "context": account.context,
            "disallow": "all",
            "allow": account.codecs,
            "direct_media": "no",
            "callerid": callerid,
            "webrtc": "yes" if browser else None,
        },
    )
    PsAuth.objects.update_or_create(
        id=account.sip_id,
        defaults={
            # Left empty: Asterisk 22 then uses "digest", which the Alembic enum cannot name.
            "auth_type": None,
            "username": account.sip_id,
            "realm": settings.PJSIP_REALM,
            "password": None,
            "md5_cred": None,
            "password_digest": password_digest(
                account.sip_id, settings.PJSIP_REALM, decrypt_secret(account.password_encrypted)
            ),
        },
    )
    PsAor.objects.update_or_create(
        id=account.sip_id,
        defaults={"max_contacts": 1, "remove_existing": "yes", "qualify_frequency": 30},
    )


def remove(sip_id: str) -> None:
    for model in (PsEndpoint, PsAuth, PsAor):
        model.objects.filter(id=sip_id).delete()


def sync(account: SipAccount, *, previous_sip_id: str | None = None) -> None:
    """Make the rows match the account. ``previous_sip_id`` clears rows left under an old id."""
    if previous_sip_id and previous_sip_id != account.sip_id:
        remove(previous_sip_id)
    if account.is_live:
        write(account)
    else:
        remove(account.sip_id)
