"""Enumerations for the notifications domain."""

import graphene

from apps.core.enums import BaseEnum


class NotificationChannelChoices(BaseEnum):
    EMAIL = "EMAIL"  # SMTP e-mail (rendered HTML)
    SMS = "SMS"  # text message via the configured SMS gateway
    INAPP = "INAPP"  # in-product feed item (no external send)


NotificationChannelEnum = graphene.Enum.from_enum(NotificationChannelChoices)


class NotificationStatusChoices(BaseEnum):
    PENDING = "PENDING"  # created, not yet (successfully) sent
    SENT = "SENT"  # delivered to the channel without error
    FAILED = "FAILED"  # the channel rejected it / errored (eligible for resend)
    SKIPPED = "SKIPPED"  # not sent: the recipient muted this category


NotificationStatusEnum = graphene.Enum.from_enum(NotificationStatusChoices)
