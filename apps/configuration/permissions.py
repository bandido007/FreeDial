"""Permissions that guard the system settings."""

from apps.core.catalogue import LEVEL_DECIDE, Group, Perm, RoleSpec

VIEW_SYSTEM_SETTINGS = "can_view_system_settings"
MANAGE_SYSTEM_SETTINGS = "can_manage_system_settings"

GROUPS = [
    Group(
        name="System Settings",
        description="Policies and labels that can be changed while the system is running.",
        permissions=[
            Perm(
                VIEW_SYSTEM_SETTINGS,
                "View system settings",
                "See every system setting and its current value. Read-only.",
            ),
            Perm(
                MANAGE_SYSTEM_SETTINGS,
                "Change system settings",
                "Change a system setting or reset it to its default. High impact: policies such "
                "as password rules apply to everyone immediately.",
                level=LEVEL_DECIDE,
            ),
        ],
    ),
]

ROLES = [
    RoleSpec(name="Security Auditor", permissions=[VIEW_SYSTEM_SETTINGS]),
]
