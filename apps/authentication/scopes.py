"""Per-application scopes (``OAUTH2_PROVIDER["SCOPES_BACKEND_CLASS"]``).

Stock django-oauth-toolkit lets any registered client request any scope. This
backend makes the rule structural — and **default-deny**:

* **first-party** — the password-grant application created by ``manage.py
  seed`` for the project's own frontend: the full configured scopes;
* **anything else** — an application hand-created in a shell or the admin, or
  left over from an old seed: **no scopes at all**, so every token request for
  it fails. An unmanaged registration is inert, not accidentally first-party.

The optional single sign-on module extends this with a third class (registered
client systems, which receive identity scopes only).
"""

from oauth2_provider.scopes import SettingsScopes

FIRST_PARTY = "first-party"
ORPHAN = "orphan"
FIRST_PARTY_SCOPES = frozenset({"read", "write"})


def is_first_party(application) -> bool:
    """The project's own frontend: a password-grant application."""
    if application is None:
        return False
    return application.authorization_grant_type == application.GRANT_PASSWORD


def classify_application(application) -> str:
    return FIRST_PARTY if is_first_party(application) else ORPHAN


class FirstPartyScopes(SettingsScopes):
    def get_available_scopes(self, application=None, request=None, *args, **kwargs):
        if application is None:  # informational listing (no client in play)
            return super().get_available_scopes(application, request, *args, **kwargs)
        if is_first_party(application):
            return super().get_available_scopes(application, request, *args, **kwargs)
        return []  # default-deny

    def get_default_scopes(self, application=None, request=None, *args, **kwargs):
        if application is None or is_first_party(application):
            return super().get_default_scopes(application, request, *args, **kwargs)
        return []
