"""Permissions the telephony app declares.

`make seed` writes them to the database; the role editor shows the group.
Every code a resolver requires must be declared here (a guard test checks).
"""

from apps.core.catalogue import Group, Perm, RoleSpec

VIEW_SIP_ACCOUNTS = "can_view_sip_accounts"
MANAGE_SIP_ACCOUNTS = "can_manage_sip_accounts"
VIEW_TRUNKS = "can_view_trunks"
MANAGE_TRUNKS = "can_manage_trunks"
DIAL_EXTERNAL = "can_dial_external"

GROUPS = [
    Group(
        name="Telephony",
        description="SIP accounts: the logins phones and the agent page register with.",
        permissions=[
            Perm(
                VIEW_SIP_ACCOUNTS,
                "View SIP accounts",
                "See every SIP account and whether it is on, never its password. Read-only.",
            ),
            Perm(
                MANAGE_SIP_ACCOUNTS,
                "Manage SIP accounts",
                "Create, change, switch off and delete SIP accounts. Takes effect on Asterisk at once.",
            ),
            Perm(
                VIEW_TRUNKS,
                "View trunks and outbound routes",
                "See the carriers (trunks), their health, and which outside numbers go through which trunk. "
                "Never a carrier's password. Read-only.",
            ),
            Perm(
                MANAGE_TRUNKS,
                "Manage trunks and outbound routes",
                "Add and change carriers and the outbound routes. Takes effect on Asterisk and the brain at once.",
            ),
            Perm(
                DIAL_EXTERNAL,
                "Call outside numbers",
                "Transfer a call to an outside number, through an outbound route that allows it.",
            ),
        ],
    ),
]

ROLES: list[RoleSpec] = [
    RoleSpec(
        name="Telephony Administrator",
        description="Looks after SIP accounts and, later, queues and entry points.",
        permissions=[VIEW_SIP_ACCOUNTS, MANAGE_SIP_ACCOUNTS, VIEW_TRUNKS, MANAGE_TRUNKS],
    ),
    RoleSpec(
        name="Contact Centre Manager",
        description="Sets up teams, agents, queues and entry points.",
        permissions=[VIEW_TRUNKS],
    ),
]
