"""Legal-consent capture (Terms of Service + Privacy notice).

Each acceptance is an immutable :class:`~apps.users.models.UserConsent` row
(document + version + when + IP + user agent). The *current* published version
of each document lives in settings, so bumping it asks everyone again on their
next consent-gated action, and the acceptance history is preserved for audit.
"""

import logging

from django.conf import settings

from apps.users.enums import ConsentDocumentChoices
from apps.users.models import UserConsent

logger = logging.getLogger(__name__)

_VERSION_SETTING = {
    ConsentDocumentChoices.TERMS.value: "CONSENT_TERMS_VERSION",
    ConsentDocumentChoices.PRIVACY.value: "CONSENT_PRIVACY_VERSION",
}
DOCUMENTS = tuple(_VERSION_SETTING)


def current_version(document: str) -> str:
    return getattr(settings, _VERSION_SETTING[document], "1")


def record_consent(*, user, document: str, ip: str | None = None, user_agent: str = "") -> UserConsent:
    """Idempotently record ``user``'s acceptance of the current ``document`` version."""
    consent, _ = UserConsent.objects.get_or_create(
        user=user,
        document=document,
        version=current_version(document),
        defaults={"ip_address": ip or None, "user_agent": user_agent or ""},
    )
    return consent


def accept_registration_consents(*, user, ip: str | None = None, user_agent: str = "") -> None:
    """Record ToS + Privacy acceptance captured at sign-up."""
    for document in DOCUMENTS:
        record_consent(user=user, document=document, ip=ip, user_agent=user_agent)
    logger.info("[accept_registration_consents] user %s accepted ToS + Privacy", user.pk)


def has_accepted_current(user, document: str) -> bool:
    return UserConsent.objects.filter(
        user=user, document=document, version=current_version(document)
    ).exists()


def pending_consents(user) -> list[str]:
    """Documents whose *current* version the user has not yet accepted.

    Non-empty after a policy-version bump (``settings.CONSENT_*_VERSION``), so
    the client can ask again and a gated action can require re-acceptance.
    """
    accepted = set(UserConsent.objects.filter(user=user).values_list("document", "version"))
    return [document for document in DOCUMENTS if (document, current_version(document)) not in accepted]


def accept_current_consents(*, user, ip: str | None = None, user_agent: str = "") -> None:
    """Record acceptance of the current version of every pending document."""
    for document in pending_consents(user):
        record_consent(user=user, document=document, ip=ip, user_agent=user_agent)
    logger.info("[accept_current_consents] user %s re-accepted current policies", user.pk)
