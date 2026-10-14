"""Permissions the agents app declares."""

from apps.core.catalogue import Group, Perm, RoleSpec

VIEW_AGENTS = "can_view_agents"
MANAGE_AGENTS = "can_manage_agents"

GROUPS = [
    Group(
        name="Agents",
        description="The agents calls are offered to.",
        permissions=[
            Perm(VIEW_AGENTS, "View agents", "See every agent and their number. Read-only."),
            Perm(MANAGE_AGENTS, "Manage agents", "Create, change and delete agents and link them to users."),
        ],
    ),
]

ROLES: list[RoleSpec] = [
    RoleSpec(
        name="Contact Centre Manager",
        description="Sets up teams, agents, queues and entry points.",
        permissions=[VIEW_AGENTS, MANAGE_AGENTS],
    ),
]
