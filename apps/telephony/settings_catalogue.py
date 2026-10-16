"""How this centre writes phone numbers (M4b), editable in Management -> System settings.

Agents type outside numbers this way when they transfer a call; outbound routes match the E.164
result. Each trunk has its own plan for the numbers its carrier sends.
"""

from apps.core.catalogue import SETTING_INTEGER, SETTING_LIST, SETTING_STRING, SettingSpec

GROUP = "Phone numbers"

SETTINGS = [
    SettingSpec(
        key="NUMBER_COUNTRY_CODE",
        group=GROUP,
        name="Country code",
        description="This centre's country, without + (Tanzania: 255).",
        type=SETTING_STRING,
        default="255",
    ),
    SettingSpec(
        key="NUMBER_NATIONAL_PREFIX",
        group=GROUP,
        name="National prefix",
        description='What a number inside the country starts with (Tanzania: "0", as in 0712 345 678).',
        type=SETTING_STRING,
        default="0",
    ),
    SettingSpec(
        key="NUMBER_INTERNATIONAL_PREFIX",
        group=GROUP,
        name="International prefix",
        description='What a number abroad starts with when dialled from here (Tanzania: "00").',
        type=SETTING_STRING,
        default="00",
    ),
    SettingSpec(
        key="NUMBER_NATIONAL_LENGTH",
        group=GROUP,
        name="National number length",
        description="Digits after the country code (Tanzania: 9).",
        type=SETTING_INTEGER,
        default=9,
        minimum=4,
        maximum=14,
    ),
    SettingSpec(
        key="NUMBER_PREMIUM_PREFIXES",
        group=GROUP,
        name="Premium-rate prefixes",
        description="E.164 prefixes no outbound route reaches unless it names them itself, e.g. +2559.",
        type=SETTING_LIST,
        default=[],
    ),
]
