"""Permissions the teams app declares.

`make seed` writes them to the database; the role editor shows the group.
Every code a resolver requires must be declared here (a guard test checks).
"""

from apps.core.catalogue import Group, Perm, RoleSpec

VIEW_TEAMS = "can_view_teams"
MANAGE_TEAMS = "can_manage_teams"

GROUPS = [
    Group(
        name="Teams",
        description="Teams of agents and the supervisors who look after them.",
        permissions=[
            Perm(VIEW_TEAMS, "View teams", "See every team, its supervisors and its agents. Read-only."),
            Perm(
                MANAGE_TEAMS,
                "Manage teams",
                "Create, rename and delete teams, and choose their supervisors and agents.",
            ),
        ],
    ),
]

ROLES: list[RoleSpec] = [
    RoleSpec(
        name="Contact Centre Manager",
        description="Sets up teams and who supervises them.",
        permissions=[VIEW_TEAMS, MANAGE_TEAMS],
    ),
]
