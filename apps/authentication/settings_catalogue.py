"""Runtime settings the authentication app declares: the security policy."""

from apps.core.catalogue import SETTING_INTEGER, SETTING_LIST, SettingSpec
from apps.core.enums import AccountTypeChoices

PASSWORDS = "Password policy"
TWO_FACTOR = "Two-factor authentication"

SETTINGS = [
    SettingSpec(
        key="PASSWORD_MIN_LENGTH",
        group=PASSWORDS,
        name="Minimum password length",
        description="The fewest characters a new password may have. Existing passwords are not affected.",
        type=SETTING_INTEGER,
        default=8,
        minimum=8,
        maximum=128,
        public=True,
    ),
    SettingSpec(
        key="PASSWORD_HISTORY_COUNT",
        group=PASSWORDS,
        name="Passwords remembered",
        description="A new password must differ from this many previous ones. 0 allows reuse.",
        type=SETTING_INTEGER,
        default=5,
        minimum=0,
        maximum=24,
    ),
    SettingSpec(
        key="PASSWORD_MAX_AGE_DAYS",
        group=PASSWORDS,
        name="Days before a password expires",
        description=(
            "After this many days a person must choose a new password at their next sign-in. "
            "0 means passwords never expire."
        ),
        type=SETTING_INTEGER,
        default=0,
        minimum=0,
        maximum=3650,
    ),
    SettingSpec(
        key="MFA_REQUIRED_ACCOUNT_TYPES",
        group=TWO_FACTOR,
        name="Account types that must use two-factor",
        description="People with these account types must set up two-factor before using the system.",
        type=SETTING_LIST,
        default=[],
        choices=tuple(AccountTypeChoices.values()),
    ),
    SettingSpec(
        key="MFA_REQUIRED_ROLES",
        group=TWO_FACTOR,
        name="Roles that must use two-factor",
        description="People holding any of these roles must set up two-factor. Enter the role names.",
        type=SETTING_LIST,
        default=[],
    ),
]
