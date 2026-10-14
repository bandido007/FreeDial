"""The frontend route map, as the backend needs to know it — deep links only.

Every in-app notification carries a ``url`` the client feeds to its router.
Those paths are a **contract** with the frontend's route table: a literal that
drifts from the router silently strands the reader on the wildcard route. This
module — together with each app's own ``notifications.py`` — is the only place
backend code is allowed to spell a frontend path, so a route rename is a
one-line change instead of a search across every ``services.py``.

Two shapes:

* plain constants for destinations that are the same for everyone;
* *resolver* factories for destinations that depend on who is reading. A
  resolver is a ``Callable[[user], str]`` that :func:`queue_for` calls once per
  recipient::

      def invoice_detail(uid):
          def resolve(user) -> str:
              base = "/admin/invoices" if is_staff_account(user) else "/portal/invoices"
              return f"{base}/{uid}"
          return resolve
"""

from apps.core.enums import STAFF_ACCOUNT_TYPES


def is_staff_account(user) -> bool:
    """Staff = Internal/System accounts — the split a frontend route guard
    usually applies to its staff-only pages."""
    return getattr(user, "account_type", None) in STAFF_ACCOUNT_TYPES


# ── Everyone-sees-the-same-page destinations ────────────────────────────────
MY_PROFILE = "/admin/profile"
"""The signed-in person's own profile."""

MY_SECURITY = "/admin/profile/security"
"""Password, two-factor authentication and signed-in devices."""

MY_ACTIVITY = "/admin/profile/activity"
"""The person's own actions and sign-in history."""

MY_NOTIFICATIONS = "/admin/notifications"
"""The in-app notification feed."""

ENDPOINT_ERRORS = "/admin/system-configuration/audit-logs"
"""The audit screens, where endpoint errors are resolved (staff)."""
