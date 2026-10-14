"""Channel send backends + a dispatcher keyed by notification channel.

Each backend module exposes ``send(notification) -> (ok: bool, remarks: str)``.
:func:`dispatch` routes a notification to the right one by its ``channel``.
"""

from apps.notifications.backends import email, inapp, sms
from apps.notifications.enums import NotificationChannelChoices

_BACKENDS = {
    NotificationChannelChoices.EMAIL.value: email.send,
    NotificationChannelChoices.SMS.value: sms.send,
    NotificationChannelChoices.INAPP.value: inapp.send,
}


def dispatch(notification) -> tuple[bool, str]:
    """Send ``notification`` via the backend for its channel."""
    sender = _BACKENDS.get(notification.channel)
    if sender is None:
        return False, f"Unsupported channel: {notification.channel}"
    return sender(notification)
