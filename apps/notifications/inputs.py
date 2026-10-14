"""GraphQL input types for the notifications domain.

One ``*Input`` per configuration entity (``uid`` present ⇒ update, absent ⇒
create) plus a ``NotificationFilterInput`` for the pageable overview. Secret
fields (SMTP password, SMS api key) are write-only inputs — they never appear on
the output types.
"""

import graphene

from apps.notifications.enums import NotificationChannelEnum, NotificationStatusEnum


# ── Notification overview filter ──────────────────────────────────────────────
class NotificationFilterInput(graphene.InputObjectType):
    uid = graphene.UUID()
    channel = graphene.Field(NotificationChannelEnum)
    status = graphene.Field(NotificationStatusEnum)
    category = graphene.String()
    is_sent = graphene.Boolean()
    receiver_uid = graphene.UUID()
    search = graphene.String(description="Free text across recipient, subject and title")
    date_from = graphene.DateTime(description="created_at >= this")
    date_to = graphene.DateTime(description="created_at <= this")


# ── EmailConfiguration ────────────────────────────────────────────────────────
class EmailConfigurationInput(graphene.InputObjectType):
    uid = graphene.UUID(description="Required when updating")
    name = graphene.String()
    host = graphene.String()
    port = graphene.Int()
    username = graphene.String()
    password = graphene.String(description="Write-only; stored encrypted")
    use_tls = graphene.Boolean()
    use_ssl = graphene.Boolean()
    verify_tls = graphene.Boolean(description="Verify the server certificate (default true)")
    from_email = graphene.String()
    from_name = graphene.String()
    is_default = graphene.Boolean()


# ── SmsConfiguration ──────────────────────────────────────────────────────────
class SmsConfigurationInput(graphene.InputObjectType):
    uid = graphene.UUID(description="Required when updating")
    name = graphene.String()
    provider = graphene.String(description="A key from getSmsProviders, e.g. LOG or MGOV")
    api_url = graphene.String(description="Blank = SMS_API_URL from the environment")
    sender_id = graphene.String(description="Blank = SMS_SENDER_ID from the environment")
    system_id = graphene.String(description="Blank = SMS_SYSTEM_ID from the environment")
    service_code = graphene.String(description="Blank = SMS_SERVICE_CODE from the environment")
    username = graphene.String()
    api_key = graphene.String(description="Write-only; stored encrypted. Blank = SMS_API_KEY")
    is_default = graphene.Boolean()


# ── NotificationTemplate ──────────────────────────────────────────────────────
class NotificationTemplateInput(graphene.InputObjectType):
    uid = graphene.UUID(description="Required when updating")
    name = graphene.String()
    category = graphene.String(description="A code from getNotificationCategories")
    channel = graphene.Field(NotificationChannelEnum)
    subject = graphene.String()
    body = graphene.String()
    is_active = graphene.Boolean()


# ── Preferences ───────────────────────────────────────────────────────────────
class NotificationPreferenceInput(graphene.InputObjectType):
    category = graphene.String(required=True)
    channel = graphene.Field(NotificationChannelEnum, required=True)
    enabled = graphene.Boolean(required=True)
