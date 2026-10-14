"""Writes and rules of the telephony app.

Keyword-only arguments, the rule checked first, the write second, an
``ApplicationError`` subclass raised with a status code when a rule says no.
Each write runs in one transaction with the PJSIP rows it causes (see
``receivers``), so a failure on either side leaves nothing half-done.
"""

import logging
import re
import secrets

from django.conf import settings
from django.db import transaction

from apps.core.crypto import encrypt_secret
from apps.core.exceptions import DuplicateError, ValidationError
from apps.telephony import pjsip, selectors
from apps.telephony.enums import SipAccountKindChoices
from apps.telephony.models import SipAccount

logger = logging.getLogger(__name__)

SIP_ID_TAKEN = 10001
BAD_SIP_ID = 10002
WEAK_PASSWORD = 10003
BAD_KIND = 10004
BAD_CODECS = 10005

MIN_PASSWORD_LENGTH = 12
SIP_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,39}$")
CODECS_PATTERN = re.compile(r"^[a-z0-9_]+(,[a-z0-9_]+)*$")
DEFAULT_CODECS = {
    SipAccountKindChoices.SOFTPHONE.value: "ulaw,alaw",
    # Not opus: this Asterisk has no codec_opus, so it could not translate opus to a ulaw customer.
    SipAccountKindChoices.BROWSER.value: "ulaw,alaw",
}


def generate_password() -> str:
    return secrets.token_urlsafe(18)  # 24 characters


def _clean_sip_id(sip_id) -> str:
    sip_id = (sip_id or "").strip()
    if not SIP_ID_PATTERN.match(sip_id):
        raise ValidationError(
            "A SIP id is 2 to 40 letters, digits, '.', '_' or '-', starting with a letter or digit.",
            code=BAD_SIP_ID,
        )
    return sip_id


def _clean_kind(kind) -> str:
    if kind not in SipAccountKindChoices.values():
        raise ValidationError("Kind must be SOFTPHONE or BROWSER.", code=BAD_KIND)
    return kind


def _clean_password(password) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"A SIP password needs at least {MIN_PASSWORD_LENGTH} characters.", code=WEAK_PASSWORD
        )
    return password


def _clean_codecs(codecs, kind) -> str:
    codecs = (codecs or "").replace(" ", "").lower() or DEFAULT_CODECS[kind]
    if not CODECS_PATTERN.match(codecs):
        raise ValidationError("Codecs are a comma-separated list, e.g. ulaw,alaw.", code=BAD_CODECS)
    return codecs


def _ensure_free(sip_id, *, except_pk=None) -> None:
    taken = SipAccount.objects.filter(sip_id=sip_id).exclude(pk=except_pk).exists()
    if taken:
        raise DuplicateError(f"SIP id {sip_id} is already in use.", code=SIP_ID_TAKEN)


def validate_sip_account(*, sip_id, kind, password=None, codecs=None, except_pk=None) -> None:
    """Check every rule without writing, for forms that show errors before saving."""
    sip_id, kind = _clean_sip_id(sip_id), _clean_kind(kind)
    _ensure_free(sip_id, except_pk=except_pk)
    if password:
        _clean_password(password)
    _clean_codecs(codecs, kind)


@transaction.atomic
def create_sip_account(
    *, actor, sip_id, kind, password=None, display_name="", codecs=None, context=None
) -> SipAccount:
    sip_id, kind = _clean_sip_id(sip_id), _clean_kind(kind)
    password = generate_password() if not password else _clean_password(password)
    _ensure_free(sip_id)
    account = SipAccount.objects.create(
        sip_id=sip_id,
        kind=kind,
        display_name=(display_name or "").strip(),
        password_encrypted=encrypt_secret(password),
        codecs=_clean_codecs(codecs, kind),
        context=(context or "").strip() or settings.PJSIP_DEFAULT_CONTEXT,
        created_by=actor,
    )
    logger.info("[telephony] SIP account %s (%s) created by %s", sip_id, kind, getattr(actor, "pk", None))
    return account


@transaction.atomic
def update_sip_account(
    *, uid, actor, sip_id=None, kind=None, password=None, display_name=None, codecs=None, context=None
) -> SipAccount:
    account = selectors.get_sip_account(uid)
    if sip_id is not None:
        account.sip_id = _clean_sip_id(sip_id)
        _ensure_free(account.sip_id, except_pk=account.pk)
    if kind is not None:
        account.kind = _clean_kind(kind)
    if password:
        account.password_encrypted = encrypt_secret(_clean_password(password))
    if display_name is not None:
        account.display_name = display_name.strip()
    if codecs is not None or kind is not None:
        account.codecs = _clean_codecs(codecs if codecs is not None else account.codecs, account.kind)
    if context is not None:
        account.context = context.strip() or settings.PJSIP_DEFAULT_CONTEXT
    account.updated_by = actor
    account.save()
    return account


@transaction.atomic
def set_sip_account_active(*, uid, actor, active: bool) -> SipAccount:
    account = selectors.get_sip_account(uid)
    account.activate() if active else account.deactivate()
    logger.info(
        "[telephony] SIP account %s %s by %s",
        account.sip_id,
        "activated" if active else "deactivated",
        getattr(actor, "pk", None),
    )
    return account


@transaction.atomic
def delete_sip_account(*, uid) -> SipAccount:
    account = selectors.get_sip_account(uid)
    account.delete()  # soft delete: the row stays for the trail, the PJSIP rows go
    return account


@transaction.atomic
def resync_pjsip() -> int:
    """Rewrite every live account's PJSIP rows, e.g. after the realtime tables were recreated."""
    accounts = list(SipAccount.objects.filter(active=True))
    for account in accounts:
        pjsip.write(account)
    return len(accounts)
