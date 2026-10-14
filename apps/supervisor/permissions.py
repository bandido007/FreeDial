"""Permissions the supervisor page declares (the names CLAUDE.md lists)."""

from apps.core.catalogue import Group, Perm, RoleSpec

MONITOR_CALLS = "can_monitor_calls"
WHISPER_CALLS = "can_whisper_calls"
BARGE_CALLS = "can_barge_calls"
FORCE_AGENT_STATE = "can_force_agent_state"
SUPERVISE = (MONITOR_CALLS, WHISPER_CALLS, BARGE_CALLS, FORCE_AGENT_STATE)  # any one opens the page

GROUPS = [
    Group(
        name="Supervision",
        description="Watching and helping the agents of one's own teams, live.",
        permissions=[
            Perm(MONITOR_CALLS, "Monitor calls", "Listen to your teams' calls; nobody hears you."),
            Perm(
                WHISPER_CALLS,
                "Whisper to agents",
                "Speak to your teams' agents during a call; the customer does not hear.",
            ),
            Perm(BARGE_CALLS, "Barge into calls", "Join your teams' calls; everyone hears you."),
            Perm(
                FORCE_AGENT_STATE,
                "Force agent state",
                "Make your teams' agents Ready, Not Ready or log them out.",
            ),
        ],
    ),
]

ROLES: list[RoleSpec] = [
    RoleSpec(
        name="Supervisor",
        description="Looks after the agents of their teams: live view, monitor, whisper, barge, force state.",
        permissions=[MONITOR_CALLS, WHISPER_CALLS, BARGE_CALLS, FORCE_AGENT_STATE, "can_view_queues"],
    ),
]
