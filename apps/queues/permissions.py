"""Permissions the queues app declares."""

from apps.core.catalogue import Group, Perm, RoleSpec

VIEW_QUEUES = "can_view_queues"
EDIT_QUEUES = "can_edit_queues"

GROUPS = [
    Group(
        name="Queues",
        description="Queues, their agents, and the numbers that lead to them.",
        permissions=[
            Perm(
                VIEW_QUEUES,
                "View queues",
                "See every queue, its settings, its agents and its entry points. Read-only.",
            ),
            Perm(
                EDIT_QUEUES,
                "Edit queues",
                "Create, change and delete queues and entry points, and choose each queue's agents.",
            ),
        ],
    ),
]

ROLES: list[RoleSpec] = [
    RoleSpec(
        name="Contact Centre Manager",
        description="Sets up teams, agents, queues and entry points.",
        permissions=[VIEW_QUEUES, EDIT_QUEUES],
    ),
]
