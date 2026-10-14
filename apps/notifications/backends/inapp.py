"""In-app delivery backend.

In-app notifications have no external transport — "sending" simply means the row
exists and is visible in the user's feed. So delivery always succeeds; the
unread/read state is carried by ``Notification.is_read`` and toggled by the
``markInAppNotificationRead`` mutation.
"""


def send(notification) -> tuple[bool, str]:
    """In-app delivery is a no-op success (the row is the delivery)."""
    return True, ""
