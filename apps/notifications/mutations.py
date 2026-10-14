"""GraphQL mutations for the notifications domain.

Thin wrappers: validate required args, delegate to services, wrap the outcome.
Surface:
  * Notification    — resend / markInAppNotificationRead
  * Preferences     — setMyNotificationPreference
  * EmailConfiguration / SmsConfiguration / NotificationTemplate — full CRUD,
    plus a "send a test message" action for each channel
"""

import logging

import graphene

from apps.core.decorators import authenticated, has_permission, log_errors_and_warnings
from apps.core.graphql.context import current_user, username
from apps.core.graphql.response import Response
from apps.core.utils import Utils
from apps.notifications import services
from apps.notifications.inputs import (
    EmailConfigurationInput,
    NotificationPreferenceInput,
    NotificationTemplateInput,
    SmsConfigurationInput,
)
from apps.notifications.permissions import MANAGE_NOTIFICATION_CONFIG, RESEND_NOTIFICATION
from apps.notifications.types import (
    EmailConfigurationType,
    NotificationPreferenceType,
    NotificationTemplateType,
    NotificationType,
    SmsConfigurationType,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# Notification actions
# ════════════════════════════════════════════════════════════════════════════
class ResendNotification(graphene.Mutation):
    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(NotificationType)

    @classmethod
    @has_permission(permissions=[RESEND_NOTIFICATION])
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        logger.info("[ResendNotification] %s by %s", uid, username(info))
        notification = services.resend(uid=uid)
        message = (
            "Notification re-sent."
            if notification.is_sent
            else ("Resend attempted but delivery failed; it will be retried automatically.")
        )
        return Response.build_response(code=9001, info=info, data=notification, custom_message=message)


class MarkInAppNotificationRead(graphene.Mutation):
    class Arguments:
        uid = graphene.UUID()
        read_all = graphene.Boolean(default_value=False)

    Output = Response.get_response_type(NotificationType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info, uid=None, read_all=False):
        count = services.mark_inapp_read(user=current_user(info), uid=uid, read_all=read_all)
        return Response.build_response(
            code=9001, info=info, custom_message=f"{count} notification(s) marked as read."
        )


class SetMyNotificationPreference(graphene.Mutation):
    """Turn one kind of notification on or off for myself, per channel."""

    class Arguments:
        input = NotificationPreferenceInput(required=True)

    Output = Response.get_response_type(NotificationPreferenceType)

    @classmethod
    @authenticated()
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        from apps.notifications import selectors

        user = current_user(info)
        services.set_preference(
            user=user, category=input.category, channel=input.channel, enabled=input.enabled
        )
        return Response.build_response(code=9001, info=info, data_list=selectors.preference_matrix(user))


class TestEmailConfiguration(graphene.Mutation):
    """Send one real test message through a mail server row."""

    class Arguments:
        uid = graphene.UUID(required=True)
        email = graphene.String(required=True)

    Output = Response.get_response_type(NotificationType)

    @classmethod
    @has_permission(permissions=[MANAGE_NOTIFICATION_CONFIG])
    @log_errors_and_warnings
    def mutate(cls, root, info, uid, email):
        notification, ok = services.send_test_email(uid=uid, to_email=email, actor=current_user(info))
        if ok:
            message = f"Test message accepted by the mail server for {notification.email_to}."
        else:
            message = f"Test message failed: {notification.remarks or 'no details from the mail server'}"
        return Response.build_response(
            code=9001 if ok else 9004, info=info, data=notification, custom_message=message
        )


# ════════════════════════════════════════════════════════════════════════════
# EmailConfiguration CRUD
# ════════════════════════════════════════════════════════════════════════════
class CreateEmailConfiguration(graphene.Mutation):
    class Arguments:
        input = EmailConfigurationInput(required=True)

    Output = Response.get_response_type(EmailConfigurationType)

    @classmethod
    @has_permission(permissions=[MANAGE_NOTIFICATION_CONFIG])
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        if not Utils.empty_and_null_checker(input, ["name", "host"]):
            return Response.build_response(code=9005, info=info)
        config = services.create_email_configuration(
            name=input.name,
            host=input.host,
            port=input.port or 587,
            username=input.username or "",
            password=input.password or "",
            use_tls=input.use_tls if input.use_tls is not None else True,
            use_ssl=input.use_ssl if input.use_ssl is not None else False,
            verify_tls=input.verify_tls if input.verify_tls is not None else True,
            from_email=input.from_email or "",
            from_name=input.from_name or "",
            is_default=input.is_default if input.is_default is not None else False,
        )
        return Response.build_response(code=9001, info=info, data=config)


class UpdateEmailConfiguration(graphene.Mutation):
    class Arguments:
        input = EmailConfigurationInput(required=True)

    Output = Response.get_response_type(EmailConfigurationType)

    @classmethod
    @has_permission(permissions=[MANAGE_NOTIFICATION_CONFIG])
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        if not Utils.empty_and_null_checker(input, ["uid"]):
            return Response.build_response(code=9005, info=info)
        config = services.update_email_configuration(
            uid=input.uid,
            name=input.name,
            host=input.host,
            port=input.port,
            username=input.username,
            password=input.password,
            use_tls=input.use_tls,
            use_ssl=input.use_ssl,
            verify_tls=input.verify_tls,
            from_email=input.from_email,
            from_name=input.from_name,
            is_default=input.is_default,
        )
        return Response.build_response(code=9001, info=info, data=config)


class DeleteEmailConfiguration(graphene.Mutation):
    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(EmailConfigurationType)

    @classmethod
    @has_permission(permissions=[MANAGE_NOTIFICATION_CONFIG])
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        config = services.delete_email_configuration(uid=uid)
        return Response.build_response(code=9001, info=info, data=config)


# ════════════════════════════════════════════════════════════════════════════
# SmsConfiguration CRUD
# ════════════════════════════════════════════════════════════════════════════
class CreateSmsConfiguration(graphene.Mutation):
    class Arguments:
        input = SmsConfigurationInput(required=True)

    Output = Response.get_response_type(SmsConfigurationType)

    @classmethod
    @has_permission(permissions=[MANAGE_NOTIFICATION_CONFIG])
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        if not Utils.empty_and_null_checker(input, ["name"]):
            return Response.build_response(code=9005, info=info)
        config = services.create_sms_configuration(
            name=input.name,
            provider=input.provider,
            api_url=input.api_url or "",
            sender_id=input.sender_id or "",
            system_id=input.system_id or "",
            service_code=input.service_code or "",
            username=input.username or "",
            api_key=input.api_key or "",
            is_default=input.is_default if input.is_default is not None else False,
        )
        return Response.build_response(code=9001, info=info, data=config)


class UpdateSmsConfiguration(graphene.Mutation):
    class Arguments:
        input = SmsConfigurationInput(required=True)

    Output = Response.get_response_type(SmsConfigurationType)

    @classmethod
    @has_permission(permissions=[MANAGE_NOTIFICATION_CONFIG])
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        if not Utils.empty_and_null_checker(input, ["uid"]):
            return Response.build_response(code=9005, info=info)
        config = services.update_sms_configuration(
            uid=input.uid,
            name=input.name,
            provider=input.provider,
            api_url=input.api_url,
            sender_id=input.sender_id,
            system_id=input.system_id,
            service_code=input.service_code,
            username=input.username,
            api_key=input.api_key,
            is_default=input.is_default,
        )
        return Response.build_response(code=9001, info=info, data=config)


class TestSmsConfiguration(graphene.Mutation):
    """Send one real test message through a gateway row (proves the
    credentials before people depend on them). The attempt is logged as a
    normal SMS notification, so the gateway's remark is visible in the log."""

    class Arguments:
        uid = graphene.UUID(required=True)
        phone = graphene.String(required=True)
        message = graphene.String(description="Optional custom text; blank = a default test line")

    Output = Response.get_response_type(NotificationType)

    @classmethod
    @has_permission(permissions=[MANAGE_NOTIFICATION_CONFIG])
    @log_errors_and_warnings
    def mutate(cls, root, info, uid, phone, message=""):
        notification, ok = services.send_test_sms(
            uid=uid, phone=phone, message=message or "", actor=current_user(info)
        )
        if ok:
            message = f"Test message accepted by the gateway for {notification.phone_to}."
        else:
            message = f"Test message failed: {notification.remarks or 'no details from the gateway'}"
        return Response.build_response(
            code=9001 if ok else 9004, info=info, data=notification, custom_message=message
        )


class DeleteSmsConfiguration(graphene.Mutation):
    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(SmsConfigurationType)

    @classmethod
    @has_permission(permissions=[MANAGE_NOTIFICATION_CONFIG])
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        config = services.delete_sms_configuration(uid=uid)
        return Response.build_response(code=9001, info=info, data=config)


# ════════════════════════════════════════════════════════════════════════════
# NotificationTemplate CRUD
# ════════════════════════════════════════════════════════════════════════════
class CreateNotificationTemplate(graphene.Mutation):
    class Arguments:
        input = NotificationTemplateInput(required=True)

    Output = Response.get_response_type(NotificationTemplateType)

    @classmethod
    @has_permission(permissions=[MANAGE_NOTIFICATION_CONFIG])
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        if not Utils.empty_and_null_checker(input, ["name", "category", "channel"]):
            return Response.build_response(code=9005, info=info)
        template = services.create_template(
            name=input.name,
            category=input.category,
            channel=input.channel,
            subject=input.subject or "",
            body=input.body or "",
            is_active=input.is_active if input.is_active is not None else True,
        )
        return Response.build_response(code=9001, info=info, data=template)


class UpdateNotificationTemplate(graphene.Mutation):
    class Arguments:
        input = NotificationTemplateInput(required=True)

    Output = Response.get_response_type(NotificationTemplateType)

    @classmethod
    @has_permission(permissions=[MANAGE_NOTIFICATION_CONFIG])
    @log_errors_and_warnings
    def mutate(cls, root, info, input):
        if not Utils.empty_and_null_checker(input, ["uid"]):
            return Response.build_response(code=9005, info=info)
        template = services.update_template(
            uid=input.uid,
            name=input.name,
            category=input.category,
            channel=input.channel,
            subject=input.subject,
            body=input.body,
            is_active=input.is_active,
        )
        return Response.build_response(code=9001, info=info, data=template)


class DeleteNotificationTemplate(graphene.Mutation):
    class Arguments:
        uid = graphene.UUID(required=True)

    Output = Response.get_response_type(NotificationTemplateType)

    @classmethod
    @has_permission(permissions=[MANAGE_NOTIFICATION_CONFIG])
    @log_errors_and_warnings
    def mutate(cls, root, info, uid):
        template = services.delete_template(uid=uid)
        return Response.build_response(code=9001, info=info, data=template)


class Mutation(graphene.ObjectType):
    # Notification actions
    resend_notification = ResendNotification.Field(description="permissions=['can_resend_notification']")
    mark_in_app_notification_read = MarkInAppNotificationRead.Field(description="Authenticated.")
    set_my_notification_preference = SetMyNotificationPreference.Field(description="Authenticated.")
    # EmailConfiguration
    create_email_configuration = CreateEmailConfiguration.Field(
        description="permissions=['can_manage_notification_config']"
    )
    update_email_configuration = UpdateEmailConfiguration.Field(
        description="permissions=['can_manage_notification_config']"
    )
    delete_email_configuration = DeleteEmailConfiguration.Field(
        description="permissions=['can_manage_notification_config']"
    )
    test_email_configuration = TestEmailConfiguration.Field(
        description="permissions=['can_manage_notification_config']: send one test e-mail through a row"
    )
    # SmsConfiguration
    create_sms_configuration = CreateSmsConfiguration.Field(
        description="permissions=['can_manage_notification_config']"
    )
    update_sms_configuration = UpdateSmsConfiguration.Field(
        description="permissions=['can_manage_notification_config']"
    )
    delete_sms_configuration = DeleteSmsConfiguration.Field(
        description="permissions=['can_manage_notification_config']"
    )
    test_sms_configuration = TestSmsConfiguration.Field(
        description="permissions=['can_manage_notification_config']: send one test SMS through a gateway row"
    )
    # NotificationTemplate
    create_notification_template = CreateNotificationTemplate.Field(
        description="permissions=['can_manage_notification_config']"
    )
    update_notification_template = UpdateNotificationTemplate.Field(
        description="permissions=['can_manage_notification_config']"
    )
    delete_notification_template = DeleteNotificationTemplate.Field(
        description="permissions=['can_manage_notification_config']"
    )
