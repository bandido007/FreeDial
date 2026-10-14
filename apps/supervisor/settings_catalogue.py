"""Supervisor attention thresholds, editable in Management → System settings."""

from apps.core.catalogue import SETTING_INTEGER, SettingSpec

SETTINGS = [
    SettingSpec(
        key="SUPERVISOR_LONG_CALL_SECONDS",
        group="Contact centre",
        name="Long call threshold",
        description="Calls longer than this many seconds appear first in the supervisor workspace (0 disables the threshold).",
        type=SETTING_INTEGER,
        default=600,
        minimum=0,
    ),
]
