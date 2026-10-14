"""Permissions that guard user management."""

from apps.core.catalogue import Group, Perm, RoleSpec

VIEW_USERS = "can_view_users"
CREATE_USER = "can_create_user"
EDIT_USER = "can_edit_user"
DELETE_USER = "can_delete_user"
ASSIGN_USER_ROLES = "can_assign_user_roles"
EXPORT_USERS = "can_export_users"

GROUPS = [
    Group(
        name="User Management",
        description="People's accounts: who can see them, create them and change them.",
        permissions=[
            Perm(
                VIEW_USERS,
                "View users",
                "See the list of user accounts, open one, and read its consent history. Read-only.",
            ),
            Perm(
                CREATE_USER,
                "Create users",
                "Create an account for someone and send them an invitation to set their password.",
            ),
            Perm(
                EDIT_USER,
                "Edit users",
                "Change a person's details, activate or deactivate the account, and suspend or "
                "block it. Does not include deleting the account.",
            ),
            Perm(
                DELETE_USER,
                "Delete users",
                "Delete an account, or restore a deleted one. High impact: the person is signed "
                "out everywhere and cannot sign in.",
            ),
            Perm(
                ASSIGN_USER_ROLES,
                "Assign roles to users",
                "Choose which roles a person holds. High impact: roles decide everything the person can do.",
            ),
            Perm(
                EXPORT_USERS,
                "Export the user list",
                "Download the user list as a PDF, Excel, CSV or Word file. Read-only, but the "
                "file holds personal data.",
            ),
        ],
    ),
]

ROLES = [
    RoleSpec(
        name="User Administrator",
        description="Creates and manages accounts and assigns roles.",
        permissions=[VIEW_USERS, CREATE_USER, EDIT_USER, DELETE_USER, ASSIGN_USER_ROLES, EXPORT_USERS],
    ),
    RoleSpec(name="Security Auditor", permissions=[VIEW_USERS]),
]
