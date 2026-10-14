"""Notification categories the authentication app sends."""

from apps.notifications.catalogue import Category, Template

PASSWORD_RESET = "PASSWORD_RESET"  # noqa: S105 — a category code, not a secret
PASSWORD_CHANGED = "PASSWORD_CHANGED"  # noqa: S105
ACCOUNT_INVITE = "ACCOUNT_INVITE"
NEW_DEVICE_LOGIN = "NEW_DEVICE_LOGIN"
MFA_CHANGED = "MFA_CHANGED"

CATEGORIES = [
    Category(
        code=PASSWORD_RESET,
        name="Password reset link",
        description="The link you asked for to choose a new password.",
        mandatory=True,
        sensitive=True,
    ),
    Category(
        code=PASSWORD_CHANGED,
        name="Password changed",
        description="Confirms that your password was changed, so you notice if it was not you.",
        mandatory=True,
        templates=[
            Template(
                "SMS",
                body="{project}: your password was changed. If this was not you, reset it now.",
            ),
        ],
    ),
    Category(
        code=ACCOUNT_INVITE,
        name="Account invitation",
        description="The link to activate an account an administrator created for you.",
        mandatory=True,
        sensitive=True,
    ),
    Category(
        code=NEW_DEVICE_LOGIN,
        name="Sign-in from a new device",
        description="Tells you when your account is signed in to from a device not seen before.",
        mandatory=True,
    ),
    Category(
        code=MFA_CHANGED,
        name="Two-factor authentication changed",
        description="Tells you when two-factor authentication is turned on, off, or reset.",
        mandatory=True,
    ),
]
