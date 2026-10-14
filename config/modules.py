"""The single list of apps that contribute to the API and the schedule.

Every app that exposes GraphQL operations or scheduled tasks is named here
once. ``config/schema.py`` and ``config/schedule.py`` both read this list, so
plugging in a new app is one line — and removing an optional module is
deleting one line (``scripts/init_project.py`` does it for you).

Order matters only for GraphQL field-name collisions (first wins); keep apps'
operation names disjoint — a guard test fails the build when they collide.
"""

# Foundation apps — shipped with the starter.
FOUNDATION_MODULES = (
    "apps.users",
    "apps.rbac",
    "apps.authentication",
    "apps.notifications",
    "apps.audit",
    "apps.attachments",
    "apps.configuration",
)

# Optional modules — included only when the app is in INSTALLED_APPS.
OPTIONAL_MODULES = ()

# Your business apps. `make app name=<name>` adds a line here.
PROJECT_MODULES = (
    "apps.telephony",
    "apps.teams",
    # <scaffold:project-modules>
)

ALL_MODULES = FOUNDATION_MODULES + OPTIONAL_MODULES + PROJECT_MODULES
