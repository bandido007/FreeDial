"""Runtime settings the users app declares."""

from apps.core.catalogue import SETTING_BOOLEAN, SETTING_LIST, SettingSpec

GROUP = "Accounts"

SETTINGS = [
    SettingSpec(
        key="ALLOW_SELF_REGISTRATION",
        group=GROUP,
        name="People may create their own account",
        description=("When off, only an administrator can create accounts and the sign-up page is closed."),
        type=SETTING_BOOLEAN,
        default=False,
        public=True,
    ),
    SettingSpec(
        key="PROFILE_REQUIRED_FIELDS",
        group=GROUP,
        name="Details a profile must have",
        description="A profile counts as complete once all of these are filled in.",
        type=SETTING_LIST,
        default=["first_name", "last_name", "phone"],
        choices=("first_name", "middle_name", "last_name", "phone", "gender", "date_of_birth", "photo"),
    ),
]
