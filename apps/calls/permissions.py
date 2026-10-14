"""Permissions the calls app declares."""

from apps.core.catalogue import Group, Perm, RoleSpec

VIEW_CALLS = "can_view_calls"
LISTEN_RECORDINGS = "can_listen_recordings"
HANDLE_CALLBACKS = "can_handle_callbacks"

GROUPS = [
    Group(
        name="Calls",
        description="Call records and the event log the brain writes.",
        permissions=[
            Perm(VIEW_CALLS, "View calls", "See every call, its legs and its events. Read-only."),
            Perm(
                LISTEN_RECORDINGS,
                "Listen to recordings",
                "Play call recordings and callers' voicemail messages.",
            ),
            Perm(
                HANDLE_CALLBACKS,
                "Handle callbacks",
                "See the callers waiting for a call back and mark them called back or cancelled.",
            ),
        ],
    ),
]

ROLES: list[RoleSpec] = [
    RoleSpec(
        name="Contact Centre Manager",
        description="Sets up teams, agents, queues and entry points.",
        permissions=[VIEW_CALLS, LISTEN_RECORDINGS, HANDLE_CALLBACKS],
    ),
]
