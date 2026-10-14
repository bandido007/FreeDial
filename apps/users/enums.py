"""Enumerations for the users domain.

The identity classification enums (account type, sign-in provider, account
status) are cross-cutting and live in :mod:`apps.core.enums`.
"""

import graphene

from apps.core.enums import BaseEnum


# Each acceptance is one auditable UserConsent row (document + version + when +
# where), so lawful basis can be proven and people are asked again when a
# policy version changes.
class ConsentDocumentChoices(BaseEnum):
    TERMS = "TERMS"  # Terms of Service
    PRIVACY = "PRIVACY"  # Privacy / data-protection notice


ConsentDocumentEnum = graphene.Enum.from_enum(ConsentDocumentChoices)
