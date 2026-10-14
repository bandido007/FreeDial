"""Settings the configuration app itself declares: how the system presents itself."""

from apps.core.catalogue import SETTING_EMAIL, SETTING_STRING, SETTING_TEXT, SETTING_URL, SettingSpec

GROUP = "Identity"

SETTINGS = [
    SettingSpec(
        key="SYSTEM_NAME",
        group=GROUP,
        name="System name",
        description="The name shown on the sign-in page and in the browser title.",
        type=SETTING_STRING,
        default="",
        public=True,
    ),
    SettingSpec(
        key="SYSTEM_TAGLINE",
        group=GROUP,
        name="Tagline",
        description="One short line shown under the system name on the sign-in page.",
        type=SETTING_STRING,
        default="",
        public=True,
    ),
    SettingSpec(
        key="SYSTEM_LOGO_PATH",
        group=GROUP,
        name="Logo",
        description="Path of the public file used as the logo. Upload it as a public file first.",
        type=SETTING_STRING,
        default="",
        public=True,
    ),
    SettingSpec(
        key="SUPPORT_EMAIL",
        group=GROUP,
        name="Support e-mail address",
        description="Where people are told to write when they need help.",
        type=SETTING_EMAIL,
        default="",
        public=True,
    ),
    SettingSpec(
        key="SUPPORT_PHONE",
        group=GROUP,
        name="Support phone number",
        description="The number people are told to call when they need help.",
        type=SETTING_STRING,
        default="",
        public=True,
    ),
    SettingSpec(
        key="TERMS_URL",
        group=GROUP,
        name="Terms of Service link",
        description="Where the Terms of Service can be read. Shown wherever consent is asked for.",
        type=SETTING_URL,
        default="",
        public=True,
    ),
    SettingSpec(
        key="PRIVACY_URL",
        group=GROUP,
        name="Privacy notice link",
        description="Where the privacy notice can be read. Shown wherever consent is asked for.",
        type=SETTING_URL,
        default="",
        public=True,
    ),
    SettingSpec(
        key="MAINTENANCE_NOTICE",
        group=GROUP,
        name="Notice to everyone",
        description="A message shown to everyone at the top of every page. Leave empty to show nothing.",
        type=SETTING_TEXT,
        default="",
        public=True,
    ),
]
