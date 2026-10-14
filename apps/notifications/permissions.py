"""Permissions that guard the notification log and its configuration."""

from apps.core.catalogue import LEVEL_DECIDE, Group, Perm, RoleSpec

VIEW_NOTIFICATIONS = "can_view_notifications"
RESEND_NOTIFICATION = "can_resend_notification"
MANAGE_NOTIFICATION_CONFIG = "can_manage_notification_config"

GROUPS = [
    Group(
        name="Notification Management",
        description="The log of e-mails and text messages, and how they are sent.",
        permissions=[
            Perm(
                VIEW_NOTIFICATIONS,
                "View the notification log",
                "See every e-mail, text message and in-app notice the system sent, with its "
                "delivery status. Read-only.",
            ),
            Perm(
                RESEND_NOTIFICATION,
                "Resend a notification",
                "Send a failed e-mail or text message again from the log. Verification codes "
                "cannot be resent this way.",
            ),
            Perm(
                MANAGE_NOTIFICATION_CONFIG,
                "Manage delivery settings",
                "Change the mail server, the SMS gateway and the message templates. High impact: "
                "a wrong setting stops every message.",
                level=LEVEL_DECIDE,
            ),
        ],
    ),
]

ROLES = [
    RoleSpec(name="Security Auditor", permissions=[VIEW_NOTIFICATIONS]),
]
