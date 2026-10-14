"""Notification categories the users app sends (see apps.notifications.catalogue)."""

from apps.notifications.catalogue import Category, Template

OTP_VERIFICATION = "OTP_VERIFICATION"
WELCOME = "WELCOME"
ACCOUNT_DELETED = "ACCOUNT_DELETED"

CATEGORIES = [
    Category(
        code=OTP_VERIFICATION,
        name="Verification code",
        description="A one-time code that proves you control an e-mail address or phone number.",
        mandatory=True,
        sensitive=True,
    ),
    Category(
        code=WELCOME,
        name="Welcome",
        description="Sent once, when your account is verified and ready to use.",
        mandatory=True,
    ),
    Category(
        code=ACCOUNT_DELETED,
        name="Account deleted",
        description="Confirms that your account was deleted, and by whom.",
        mandatory=True,
        templates=[
            Template(
                "SMS",
                body="{project}: your account has been deleted. If this was not expected, "
                "contact the administrator.",
            ),
        ],
    ),
]
