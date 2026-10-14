"""Seed step for `manage.py seed`: delivery defaults.

* a **LOG-provider SMS configuration** when none exists and the environment
  names no gateway — so the SMS channel always *works* in development
  (payloads land in the log) and production edits the row to a real provider.
* the **default templates** declared by every app's ``notifications.py``.

Never touches an existing configuration or an edited template.
"""

from apps.notifications import catalogue, services
from apps.notifications.backends import sms as sms_backend
from apps.notifications.models import SmsConfiguration

ORDER = 30


def run(command) -> None:
    issues = catalogue.problems()
    if issues:
        raise RuntimeError("Notification categories are invalid:\n- " + "\n- ".join(issues))

    if not SmsConfiguration.objects.exists():
        if sms_backend.env_gateway()["api_url"]:
            command.stdout.write("  • SMS gateway comes from the environment; no row created")
        else:
            SmsConfiguration.objects.create(
                name="Log only (development)", provider=sms_backend.PROVIDER_LOG, is_default=True
            )
            command.stdout.write("  • LOG SMS configuration created")
    command.stdout.write(f"  • {len(catalogue.categories())} notification categories registered")
    services.install_default_templates(log=command.stdout.write)
