"""Permissions the setup app declares.

`make seed` writes them to the database; the role editor shows the group.
"""

from apps.core.catalogue import Group, Perm, RoleSpec

VIEW_SETUP_GUIDE = "can_view_setup_guide"
CONFIRM_SETUP_TESTS = "can_confirm_setup_tests"

GROUPS = [
    Group(
        name="Setup guide",
        description="The admin's user manual and the checklist that shows what is set up and tested.",
        permissions=[
            Perm(
                VIEW_SETUP_GUIDE,
                "View the setup guide",
                "Read the user manual and the setup checklist (what is set up, what needs attention, "
                "which tests have passed). Read-only.",
            ),
            Perm(
                CONFIRM_SETUP_TESTS,
                "Confirm setup tests",
                "Mark a hands-on test of the checklist as working (for example: both sides heard each "
                "other), or clear that mark. Who confirmed and when is kept.",
            ),
        ],
    ),
]

ROLES: list[RoleSpec] = [
    RoleSpec(
        name="Contact Centre Manager",
        description="Sets up teams, agents, queues and entry points.",
        permissions=[VIEW_SETUP_GUIDE, CONFIRM_SETUP_TESTS],
    ),
    RoleSpec(
        name="Telephony Administrator",
        description="Looks after SIP accounts and, later, queues and entry points.",
        permissions=[VIEW_SETUP_GUIDE],
    ),
    RoleSpec(
        name="User Administrator",
        description="Creates and manages accounts and assigns roles.",
        permissions=[VIEW_SETUP_GUIDE],
    ),
]
