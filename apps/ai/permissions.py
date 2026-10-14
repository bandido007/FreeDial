"""Permissions the AI agents app declares."""

from apps.core.catalogue import Group, Perm, RoleSpec

VIEW_AI_AGENTS = "can_view_ai_agents"
EDIT_AI_AGENTS = "can_edit_ai_agents"

GROUPS = [
    Group(
        name="AI agents",
        description="The AI agents that answer entry points and hand callers to people.",
        permissions=[
            Perm(
                VIEW_AI_AGENTS,
                "View AI agents",
                "See each AI agent's instructions, routes and voice. Read-only.",
            ),
            Perm(
                EDIT_AI_AGENTS,
                "Edit AI agents",
                "Create and change AI agents: what they know, where they may send callers, their voice and models.",
            ),
        ],
    ),
]

ROLES: list[RoleSpec] = [
    RoleSpec(
        name="Contact Centre Manager",
        description="Sets up teams, agents, queues and entry points.",
        permissions=[VIEW_AI_AGENTS, EDIT_AI_AGENTS],
    ),
]
