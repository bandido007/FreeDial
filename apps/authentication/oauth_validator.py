"""Project OAuth2 validator (``OAUTH2_PROVIDER["OAUTH2_VALIDATOR_CLASS"]``).

On top of the stock validator it **gates every token use, not just issuance**.
``validate_bearer_token`` is the single choke point behind the GraphQL
authentication backend and the REST ``OAuth2Authentication`` — so suspending,
blocking, deactivating or deleting an account takes effect on the very next
request, whichever code path flipped the flag.
"""

from oauth2_provider.oauth2_validators import OAuth2Validator

from apps.authentication.gates import blocked_reason


class FirstPartyOAuth2Validator(OAuth2Validator):
    def validate_bearer_token(self, token, scopes, request):
        if not super().validate_bearer_token(token, scopes, request):
            return False
        user = getattr(request, "user", None)
        return user is None or blocked_reason(user) is None
