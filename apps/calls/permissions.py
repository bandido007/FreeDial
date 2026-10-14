"""Permissions the calls app declares."""

from apps.core.catalogue import Group, Perm, RoleSpec

VIEW_CALLS = "can_view_calls"

GROUPS = [
    Group(
        name="Calls",
        description="Call records and the event log the brain writes.",
        permissions=[
            Perm(VIEW_CALLS, "View calls", "See every call, its legs and its events. Read-only."),
        ],
    ),
]

ROLES: list[RoleSpec] = []
