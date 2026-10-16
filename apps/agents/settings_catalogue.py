"""The agent number range, editable in Management → System settings."""

from apps.core.catalogue import SETTING_INTEGER, SettingSpec

SETTINGS = [
    SettingSpec(
        key="AGENT_NUMBER_FIRST",
        group="Contact centre",
        name="Agent numbers: first",
        description=(
            "The Add agent form suggests the next free number from here up to the last. Keep the range "
            "clear of numbers people dial: entry numbers (800 to 899) and phones (such as 1000 to 1009)."
        ),
        type=SETTING_INTEGER,
        default=2000,
        minimum=10,
    ),
    SettingSpec(
        key="AGENT_NUMBER_LAST",
        group="Contact centre",
        name="Agent numbers: last",
        description="The highest number the Add agent form suggests. Any number can still be typed by hand.",
        type=SETTING_INTEGER,
        default=6999,
        minimum=10,
    ),
]
