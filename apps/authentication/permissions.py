"""Permissions that guard sign-in administration."""

from apps.core.catalogue import LEVEL_DECIDE, Group, Perm, RoleSpec

VIEW_LOGIN_HISTORY = "can_view_login_history"
RESET_USER_MFA = "can_reset_user_mfa"
RESET_USER_PASSWORD = "can_reset_user_password"  # noqa: S105 — a permission code
REVOKE_USER_SESSIONS = "can_revoke_user_sessions"
VIEW_LDAP_CONFIG = "can_view_ldap_config"
MANAGE_LDAP_CONFIG = "can_manage_ldap_config"
PULL_LDAP_USERS = "can_pull_ldap_users"

GROUPS = [
    Group(
        name="Sign-in & Security",
        description="Sign-in history, and helping people who are locked out.",
        permissions=[
            Perm(
                VIEW_LOGIN_HISTORY,
                "View sign-in history",
                "See who signed in, when, and from which device, plus failed attempts and other "
                "security events. Read-only.",
            ),
            Perm(
                RESET_USER_MFA,
                "Reset two-factor authentication",
                "Turn off two-factor authentication for someone who lost their device. High "
                "impact: the account is protected by its password alone until they set it up again.",
            ),
            Perm(
                RESET_USER_PASSWORD,
                "Send a password reset",
                "E-mail someone a link to choose a new password. You never see or set the password.",
            ),
            Perm(
                REVOKE_USER_SESSIONS,
                "Sign a user out everywhere",
                "End every active session of another person. They must sign in again on each device.",
                level=LEVEL_DECIDE,
            ),
        ],
    ),
    Group(
        name="Directory / LDAP",
        description="Directories that people sign in against.",
        permissions=[
            Perm(
                VIEW_LDAP_CONFIG,
                "View directories",
                "See the configured directories. The bind password is never shown. Read-only.",
            ),
            Perm(
                MANAGE_LDAP_CONFIG,
                "Manage directories",
                "Add, change or remove a directory. High impact: a wrong setting stops everyone "
                "in that directory from signing in.",
                level=LEVEL_DECIDE,
            ),
            Perm(
                PULL_LDAP_USERS,
                "Import users from a directory",
                "Create accounts for the people found in a directory. The accounts start inactive "
                "until someone activates them.",
            ),
        ],
    ),
]

ROLES = [
    RoleSpec(
        name="User Administrator",
        permissions=[VIEW_LOGIN_HISTORY, RESET_USER_MFA, RESET_USER_PASSWORD, REVOKE_USER_SESSIONS],
    ),
    RoleSpec(name="Security Auditor", permissions=[VIEW_LOGIN_HISTORY, VIEW_LDAP_CONFIG]),
]
