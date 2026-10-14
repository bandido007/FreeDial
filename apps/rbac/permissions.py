"""Permissions that guard role management (see apps.core.catalogue)."""

from apps.core.catalogue import Group, Perm, RoleSpec

VIEW_ROLES = "can_view_roles"
CREATE_ROLE = "can_create_role"
EDIT_ROLE = "can_edit_role"
DELETE_ROLE = "can_delete_role"
ASSIGN_ROLE_PERMISSIONS = "can_assign_role_permissions"

# The role that always holds every permission. Created and topped up by
# `manage.py sync_rbac`; it cannot be edited or deleted from the screens.
SYSTEM_ROLE_NAME = "System Administrator"

GROUPS = [
    Group(
        name="Role Management",
        description="Roles and the permissions each role carries.",
        permissions=[
            Perm(
                VIEW_ROLES,
                "View roles",
                "See the list of roles and the permissions each one carries. Read-only.",
            ),
            Perm(
                CREATE_ROLE,
                "Create roles",
                "Add a new role from the Roles page. Does not include giving it permissions.",
            ),
            Perm(
                EDIT_ROLE,
                "Edit roles",
                "Rename a role or change its description. System roles stay locked.",
            ),
            Perm(
                DELETE_ROLE,
                "Delete roles",
                "Remove a role nobody holds. High impact: the role disappears from every screen.",
            ),
            Perm(
                ASSIGN_ROLE_PERMISSIONS,
                "Change a role's permissions",
                "Tick or untick permissions on a role. High impact: it changes what every "
                "holder of that role can do.",
            ),
        ],
    ),
]

ROLES = [
    RoleSpec(
        name="Security Auditor",
        description="Reads roles, users and the audit trail. Cannot change anything.",
        permissions=[VIEW_ROLES],
    ),
]
