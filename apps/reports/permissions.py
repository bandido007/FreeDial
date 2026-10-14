"""Permissions the reports app declares."""

from apps.core.catalogue import Group, Perm, RoleSpec

VIEW_REPORTS = "can_view_reports"

GROUPS = [
    Group(
        name="Reports",
        description="Contact-centre reports built from the event log.",
        permissions=[
            Perm(
                VIEW_REPORTS,
                "View reports",
                "See and download reports: calls per queue, service level, abandon rate, handle time, "
                "agent time in state and the AI's containment and handoff reasons. Read-only.",
            ),
        ],
    ),
]

ROLES: list[RoleSpec] = [
    RoleSpec(
        name="Contact Centre Manager",
        description="Sets up teams, agents, queues and entry points.",
        permissions=[VIEW_REPORTS],
    ),
]
