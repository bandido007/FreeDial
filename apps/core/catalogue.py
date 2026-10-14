"""Building blocks for an app's permission catalogue.

Each app declares the permissions it guards its operations with in its own
``permissions.py`` — never in a shared file::

    # apps/invoices/permissions.py
    from apps.core.catalogue import Group, Perm, RoleSpec

    VIEW_INVOICES = "can_view_invoices"
    APPROVE_INVOICES = "can_approve_invoices"

    GROUPS = [
        Group(
            name="Invoices",
            description="Who can see, prepare and approve invoices.",
            permissions=[
                Perm(VIEW_INVOICES, "View invoices",
                     "See the invoice list and open an invoice. Read-only."),
                Perm(APPROVE_INVOICES, "Approve invoices",
                     "Approve an invoice for payment. High impact: it releases money."),
            ],
        ),
    ]

    ROLES = [
        RoleSpec(name="Finance Officer", description="Prepares and approves invoices.",
                 permissions=[VIEW_INVOICES, APPROVE_INVOICES]),
    ]

``python manage.py sync_rbac`` collects every app's ``GROUPS`` and ``ROLES``
and writes them to the database. Resolvers import the code constants from the
same file, so a permission is spelled exactly once.

Writing for the reader
----------------------
A permission is read by the person ticking it on a role, not by a developer:

* ``name`` — a short verb phrase, at most 45 characters, no slashes.
* ``description`` — one or two plain sentences: what it allows, where, and what
  it does **not** include. End every ``can_view_*`` with "Read-only." and say
  "High impact" where a mistake is costly.

A guard test rejects terse or missing copy.
"""

from dataclasses import dataclass, field

LEVEL_VIEW = "VIEW"  # look, never change
LEVEL_CHANGE = "CHANGE"  # create, edit, record, act
LEVEL_DECIDE = "DECIDE"  # approve, publish, delete, grant access — high impact
LEVELS = (LEVEL_VIEW, LEVEL_CHANGE, LEVEL_DECIDE)

# The verb prefix decides the level unless the permission states one.
_DECIDE_PREFIXES = (
    "can_approve_",
    "can_publish_",
    "can_delete_",
    "can_terminate_",
    "can_administer_",
    "can_assign_",
    "can_reset_",
)
_VIEW_PREFIXES = ("can_view_", "can_export_")


def derive_level(code: str) -> str:
    """``VIEW`` / ``CHANGE`` / ``DECIDE`` from a permission code's verb."""
    if code.startswith(_DECIDE_PREFIXES):
        return LEVEL_DECIDE
    if code.startswith(_VIEW_PREFIXES):
        return LEVEL_VIEW
    return LEVEL_CHANGE


@dataclass(frozen=True)
class Perm:
    """One capability. ``level`` overrides the level derived from the code."""

    code: str
    name: str
    description: str
    level: str = ""

    @property
    def access_level(self) -> str:
        return self.level or derive_level(self.code)


@dataclass(frozen=True)
class Group:
    """A titled set of permissions, shown as one section of the role editor."""

    name: str
    description: str
    permissions: list[Perm] = field(default_factory=list)


@dataclass(frozen=True)
class RoleSpec:
    """A role an app wants to exist, with the permissions it starts with.

    ``permissions`` lists codes from *any* app. Several apps may contribute to
    the same role name: their permission lists are merged. ``locked`` roles
    cannot be edited or deleted from the screens.
    """

    name: str
    description: str = ""
    permissions: list[str] = field(default_factory=list)
    locked: bool = False


# ════════════════════════════════════════════════════════════════════════════
# Runtime settings
# ════════════════════════════════════════════════════════════════════════════
# A value an administrator may change while the system is running — a policy,
# a switch, a label — as opposed to deployment configuration (hosts, keys,
# passwords), which belongs in the environment and nowhere else.
#
# Each app declares its settings in its own ``settings_catalogue.py``::
#
#     from apps.core.catalogue import SettingSpec
#
#     SETTINGS = [
#         SettingSpec(
#             key="INVOICE_APPROVAL_LIMIT",
#             group="Invoices",
#             name="Approval limit",
#             description="Invoices above this amount need a second approver.",
#             type="INTEGER", default=1_000_000, minimum=0,
#         ),
#     ]
#
# and reads the effective value with ``apps.core.hooks.policy``::
#
#     limit = policy("INVOICE_APPROVAL_LIMIT", 1_000_000)
#
# Order of precedence: the value saved on the System settings screen → the
# Django setting of the same name → the default.

SETTING_BOOLEAN = "BOOLEAN"
SETTING_INTEGER = "INTEGER"
SETTING_STRING = "STRING"
SETTING_TEXT = "TEXT"
SETTING_CHOICE = "CHOICE"
SETTING_LIST = "LIST"  # a list of strings
SETTING_EMAIL = "EMAIL"
SETTING_URL = "URL"
SETTING_TYPES = (
    SETTING_BOOLEAN,
    SETTING_INTEGER,
    SETTING_STRING,
    SETTING_TEXT,
    SETTING_CHOICE,
    SETTING_LIST,
    SETTING_EMAIL,
    SETTING_URL,
)


@dataclass(frozen=True)
class SettingSpec:
    """One runtime setting.

    ``public`` settings are readable without signing in (what the sign-in page
    needs: the system's name, a support address). Never mark anything
    confidential as public, and never store a secret as a setting.
    """

    key: str
    group: str
    name: str
    description: str
    type: str = SETTING_STRING
    default: object = None
    choices: tuple = ()
    minimum: int | None = None
    maximum: int | None = None
    public: bool = False
