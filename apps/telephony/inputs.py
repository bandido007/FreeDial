"""GraphQL input types of the telephony app."""

import graphene

from apps.telephony.enums import SipAccountKindEnum


class SipAccountInput(graphene.InputObjectType):
    uid = graphene.UUID(description="Required when updating")
    sip_id = graphene.String(description="Required when creating: endpoint id and SIP username")
    kind = SipAccountKindEnum(description="Required when creating")
    password = graphene.String(description="At least 12 characters; left out on create = generated")
    display_name = graphene.String()
    codecs = graphene.String(description="Comma-separated, e.g. ulaw,alaw; empty = the kind's default")
    context = graphene.String(description="Dialplan context; empty = the default")


class SipAccountFilterInput(graphene.InputObjectType):
    uid = graphene.UUID()
    kind = SipAccountKindEnum()
    active = graphene.Boolean()
    search = graphene.String(description="Free text across SIP id and display name")
