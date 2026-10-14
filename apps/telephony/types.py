"""GraphQL output types of the telephony app. The SIP password is never an output."""

import graphene
from graphene_django import DjangoObjectType

from apps.telephony.enums import SipAccountKindEnum
from apps.telephony.models import SipAccount


class SipAccountType(DjangoObjectType):
    kind = SipAccountKindEnum()
    created_by_name = graphene.String()

    class Meta:
        model = SipAccount
        fields = (
            "uid",
            "sip_id",
            "kind",
            "display_name",
            "codecs",
            "context",
            "active",
            "created_at",
            "updated_at",
        )

    def resolve_created_by_name(self, info):
        return self.created_by.get_full_name() if self.created_by_id else ""
