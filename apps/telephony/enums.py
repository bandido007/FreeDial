"""Enumerations of the telephony app (model choices + their GraphQL enums)."""

import graphene

from apps.core.enums import BaseEnum


class SipAccountKindChoices(BaseEnum):
    SOFTPHONE = "SOFTPHONE"  # a SIP app or desk phone over UDP (Zoiper, Linphone)
    BROWSER = "BROWSER"  # the agent page's phone: WebRTC over WSS


SipAccountKindEnum = graphene.Enum.from_enum(SipAccountKindChoices)
