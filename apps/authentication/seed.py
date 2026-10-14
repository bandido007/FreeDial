"""Seed step for `manage.py seed`: the first-party OAuth2 application.

The project's own frontend signs people in with the password grant against
this application. It is created once; its client ID is printed so it can be
put in the frontend's configuration. Set ``OAUTH_CLIENT_ID`` to pin the value.
"""

from django.conf import settings
from oauth2_provider.models import get_application_model

ORDER = 20


def run(command) -> None:
    Application = get_application_model()
    name = settings.OAUTH_APP_NAME
    client_id = settings.OAUTH_CLIENT_ID
    defaults = {
        "client_type": Application.CLIENT_PUBLIC,
        "authorization_grant_type": Application.GRANT_PASSWORD,
    }
    if client_id:
        defaults["client_id"] = client_id
    application, created = Application.objects.get_or_create(name=name, defaults=defaults)
    if client_id and not created and application.client_id != client_id:
        application.client_id = client_id
        application.save(update_fields=["client_id"])
    state = "created" if created else "exists"
    command.stdout.write(f"  • OAuth2 application '{name}' {state} — client_id: {application.client_id}")
