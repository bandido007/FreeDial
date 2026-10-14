"""Scope-aware OAuth2 authentication backend (first-party API guard).

The stock ``OAuth2Backend`` authenticates *any* valid access token — including
an identity-only token minted for an external system by the single sign-on
module (scopes ``openid profile email``). Such a token proves who the user is
**to that system**; it must never double as an API key to this backend, or
every connected system could read the whole API as the user.

This backend therefore admits a bearer token to Django authentication (the
``OAuth2TokenMiddleware`` → GraphQL path) only when it carries a first-party
scope (``read`` / ``write``).

REST endpoints authenticate separately (``OAuth2Authentication``); they carry
``TokenHasReadWriteScope`` in their permission classes for the same guard.
"""

from django.core.exceptions import SuspiciousOperation
from oauth2_provider.backends import OAuth2Backend
from oauth2_provider.oauth2_backends import get_oauthlib_core

from apps.authentication.scopes import FIRST_PARTY_SCOPES


class FirstPartyOAuth2Backend(OAuth2Backend):
    def authenticate(self, request=None, **credentials):
        if request is None:
            return None
        try:
            valid, oauth_request = get_oauthlib_core().verify_request(request, scopes=[])
        except ValueError as error:
            if str(error) == "Invalid hex encoding in query string.":
                raise SuspiciousOperation(error) from error
            raise
        if not valid:
            return None
        token = getattr(oauth_request, "access_token", None)
        token_scopes = set(token.scope.split()) if token else set()
        if not token_scopes & FIRST_PARTY_SCOPES:
            return None
        return oauth_request.user
