"""Read-side logic (selectors) for the notifications domain.

Selectors answer questions about the data and never mutate it. List selectors
accept an optional ``Q`` so the GraphQL layer can layer search filters on top;
single-row fetchers raise :class:`NotFoundError`.
"""

from django.db.models import Q, QuerySet

from apps.core.exceptions import NotFoundError
from apps.notifications import catalogue
from apps.notifications.enums import NotificationChannelChoices
from apps.notifications.models import (
    EmailConfiguration,
    Notification,
    NotificationPreference,
    NotificationTemplate,
    SmsConfiguration,
)


# ── single-row fetch helpers (raise if absent) ──────────────────────────────
def get_notification(uid) -> Notification:
    obj = Notification.objects.filter(uid=uid).first()
    if obj is None:
        raise NotFoundError("Notification not found")
    return obj


def get_email_configuration(uid) -> EmailConfiguration:
    obj = EmailConfiguration.objects.filter(uid=uid).first()
    if obj is None:
        raise NotFoundError("Email configuration not found")
    return obj


def get_sms_configuration(uid) -> SmsConfiguration:
    obj = SmsConfiguration.objects.filter(uid=uid).first()
    if obj is None:
        raise NotFoundError("SMS configuration not found")
    return obj


def get_template(uid) -> NotificationTemplate:
    obj = NotificationTemplate.objects.filter(uid=uid).first()
    if obj is None:
        raise NotFoundError("Notification template not found")
    return obj


# ── list querysets ──────────────────────────────────────────────────────────
def notifications_queryset(filters: Q | None = None) -> QuerySet[Notification]:
    qs = Notification.objects.all().select_related("receiver")
    return qs.filter(filters) if filters is not None else qs


def my_notifications(user, channel: str | None = None) -> QuerySet[Notification]:
    qs = Notification.objects.filter(receiver=user)
    return qs.filter(channel=channel) if channel else qs


def unread_in_app_count(user) -> int:
    return Notification.objects.filter(
        receiver=user, channel=NotificationChannelChoices.INAPP.value, is_read=False
    ).count()


def email_configs_queryset(filters: Q | None = None) -> QuerySet[EmailConfiguration]:
    qs = EmailConfiguration.objects.all()
    return qs.filter(filters) if filters is not None else qs


def sms_configs_queryset(filters: Q | None = None) -> QuerySet[SmsConfiguration]:
    qs = SmsConfiguration.objects.all()
    return qs.filter(filters) if filters is not None else qs


def templates_queryset(filters: Q | None = None) -> QuerySet[NotificationTemplate]:
    qs = NotificationTemplate.objects.all()
    return qs.filter(filters) if filters is not None else qs


def active_template(category: str, channel: str) -> NotificationTemplate | None:
    """The active template for a (category, channel) pair, if one exists."""
    return NotificationTemplate.objects.filter(category=category, channel=channel, is_active=True).first()


# ── preferences ─────────────────────────────────────────────────────────────
def wants(user, category: str, channel: str) -> bool:
    """Whether ``user`` should receive ``category`` on ``channel``.

    Mandatory categories always go out. Otherwise the answer is yes unless the
    person switched this pair off.
    """
    if user is None or not category or catalogue.is_mandatory(category):
        return True
    muted = NotificationPreference.objects.filter(
        user=user, category=category, channel=channel, enabled=False
    ).exists()
    return not muted


def preference_matrix(user) -> list[dict]:
    """Every category × channel with the person's current choice (for the
    preferences screen)."""
    stored = {
        (pref.category, pref.channel): pref.enabled
        for pref in NotificationPreference.objects.filter(user=user)
    }
    rows = []
    for category in sorted(catalogue.categories(), key=lambda item: item.name):
        for channel in NotificationChannelChoices.values():
            rows.append(
                {
                    "category": category.code,
                    "name": category.name,
                    "description": category.description,
                    "channel": channel,
                    "mandatory": category.mandatory,
                    "enabled": True if category.mandatory else stored.get((category.code, channel), True),
                }
            )
    return rows
