"""GraphQL output types for the notifications domain.

Configuration types deliberately **omit** the encrypted secret columns
(``password_encrypted`` / ``api_key_encrypted``) so secrets can never leak
through the API — they are write-only via the inputs.
"""

import graphene
from graphene_django import DjangoObjectType

from apps.notifications import catalogue
from apps.notifications.backends import sms as sms_backend
from apps.notifications.enums import NotificationChannelEnum, NotificationStatusEnum
from apps.notifications.models import (
    EmailConfiguration,
    Notification,
    NotificationTemplate,
    SmsConfiguration,
)


class NotificationType(DjangoObjectType):
    channel = NotificationChannelEnum()
    status = NotificationStatusEnum()
    category = graphene.String()
    category_name = graphene.String(description="The category as a person reads it.")
    receiver_uid = graphene.UUID()
    receiver_name = graphene.String()

    class Meta:
        model = Notification
        fields = (
            "uid",
            "channel",
            "category",
            "status",
            "title",
            "subject",
            "message",
            "email_to",
            "phone_to",
            "full_name",
            "url",
            "attempts",
            "remarks",
            "sent_time",
            "is_sent",
            "is_read",
            "is_redacted",
            "created_at",
        )

    def resolve_category(self, info):
        return self.category or None

    def resolve_category_name(self, info):
        category = catalogue.get(self.category)
        return category.name if category else (self.category or None)

    def resolve_receiver_uid(self, info):
        return self.receiver.uid if self.receiver_id else None

    def resolve_receiver_name(self, info):
        return self.receiver.get_full_name() if self.receiver_id else self.full_name


class UnreadCountType(graphene.ObjectType):
    unread = graphene.Int()


class EmailConfigurationType(DjangoObjectType):
    has_password = graphene.Boolean()

    class Meta:
        model = EmailConfiguration
        fields = (
            "uid",
            "name",
            "host",
            "port",
            "username",
            "use_tls",
            "use_ssl",
            "verify_tls",
            "from_email",
            "from_name",
            "is_default",
            "active",
            "created_at",
        )

    def resolve_has_password(self, info):
        return bool(self.password_encrypted)


class SmsProviderType(graphene.ObjectType):
    """A gateway implementation the SMS configuration screen can select."""

    key = graphene.String()
    name = graphene.String()
    required_settings = graphene.List(graphene.String)


class SmsConfigurationType(DjangoObjectType):
    has_api_key = graphene.Boolean(description="An API key is stored on the row (never returned).")
    is_ready = graphene.Boolean(
        description="Every gateway setting resolves — from the row or from the environment — "
        "so a send can go out."
    )
    env_fallbacks = graphene.List(
        graphene.String, description="Gateway settings this row leaves to the SMS_* environment values."
    )
    missing_settings = graphene.List(
        graphene.String, description="Gateway settings neither the row nor the environment provide."
    )

    class Meta:
        model = SmsConfiguration
        fields = (
            "uid",
            "name",
            "provider",
            "api_url",
            "sender_id",
            "system_id",
            "service_code",
            "username",
            "is_default",
            "active",
            "created_at",
        )

    def resolve_has_api_key(self, info):
        return bool(self.api_key_encrypted)

    def resolve_is_ready(self, info):
        if self.provider == sms_backend.PROVIDER_LOG:
            return sms_backend.log_provider_allowed()
        return sms_backend.resolve_gateway(self).ready

    def resolve_env_fallbacks(self, info):
        if self.provider == sms_backend.PROVIDER_LOG:
            return []
        return sms_backend.resolve_gateway(self).from_env

    def resolve_missing_settings(self, info):
        if self.provider == sms_backend.PROVIDER_LOG:
            return []
        return sms_backend.resolve_gateway(self).missing


class NotificationTemplateType(DjangoObjectType):
    category = graphene.String()
    category_name = graphene.String()
    channel = NotificationChannelEnum()

    class Meta:
        model = NotificationTemplate
        fields = (
            "uid",
            "name",
            "category",
            "channel",
            "subject",
            "body",
            "is_active",
            "active",
            "created_at",
        )

    def resolve_category_name(self, info):
        category = catalogue.get(self.category)
        return category.name if category else self.category


class NotificationCategoryType(graphene.ObjectType):
    """A kind of message an app can send (declared in code)."""

    code = graphene.String()
    name = graphene.String()
    description = graphene.String()
    mandatory = graphene.Boolean(description="Cannot be muted by the recipient.")
    sensitive = graphene.Boolean(description="Carries a one-time secret; never replayed.")


class NotificationPreferenceType(graphene.ObjectType):
    """One row of the preferences screen: a category on a channel."""

    category = graphene.String()
    name = graphene.String()
    description = graphene.String()
    channel = NotificationChannelEnum()
    mandatory = graphene.Boolean()
    enabled = graphene.Boolean()
