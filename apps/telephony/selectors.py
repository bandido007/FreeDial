"""Reads of the telephony app. Selectors never mutate."""

from django.db.models import Q, QuerySet

from apps.core.exceptions import NotFoundError
from apps.telephony.models import SipAccount

NOT_FOUND = 10000


def get_sip_account(uid) -> SipAccount:
    account = SipAccount.objects.select_related("created_by").filter(uid=uid).first()
    if account is None:
        raise NotFoundError("SIP account not found", code=NOT_FOUND)
    return account


def sip_accounts_queryset(filters: Q | None = None) -> QuerySet[SipAccount]:
    qs = SipAccount.objects.select_related("created_by")
    return qs.filter(filters) if filters is not None else qs
