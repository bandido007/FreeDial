"""Permissions the agents app declares."""

from apps.core.catalogue import Group, Perm, RoleSpec

VIEW_AGENTS = "can_view_agents"
MANAGE_AGENTS = "can_manage_agents"
TAKE_CALLS = "can_take_calls"

GROUPS = [
    Group(
        name="Agents",
        description="The agents calls are offered to.",
        permissions=[
            Perm(VIEW_AGENTS, "View agents", "See every agent and their number. Read-only."),
            Perm(
                MANAGE_AGENTS,
                "Manage agents",
                "Create, change and delete agents, Not Ready reasons and wrap-up codes.",
            ),
            Perm(
                TAKE_CALLS,
                "Take calls",
                "Sign in to the agent page with a browser phone, go Ready and handle calls.",
            ),
        ],
    ),
]

ROLES: list[RoleSpec] = [
    RoleSpec(
        name="Agent",
        description="Answers customers' calls on the agent page.",
        permissions=[TAKE_CALLS],
    ),
    RoleSpec(
        name="Contact Centre Manager",
        description="Sets up teams, agents, queues and entry points.",
        permissions=[VIEW_AGENTS, MANAGE_AGENTS],
    ),
]
