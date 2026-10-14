"""Permissions the telephony app declares.

`make seed` writes them to the database; the role editor shows the group.
Every code a resolver requires must be declared here (a guard test checks).
"""

from apps.core.catalogue import Group, Perm, RoleSpec

VIEW_SIP_ACCOUNTS = "can_view_sip_accounts"
MANAGE_SIP_ACCOUNTS = "can_manage_sip_accounts"

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
        ],
    ),
]

ROLES: list[RoleSpec] = [
    RoleSpec(
        name="Telephony Administrator",
        description="Looks after SIP accounts and, later, queues and entry points.",
        permissions=[VIEW_SIP_ACCOUNTS, MANAGE_SIP_ACCOUNTS],
    ),
]
