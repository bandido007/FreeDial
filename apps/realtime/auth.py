"""Who may open a live WebSocket: the same bearer token and rules as the API."""

from oauth2_provider.models import AccessToken

from apps.agents.models import Agent
from apps.agents.permissions import TAKE_CALLS
from apps.authentication import gates
from apps.authentication.scopes import FIRST_PARTY_SCOPES
from apps.core.hooks import resolve_permissions


def agent_for_token(token: str) -> Agent | None:
    """The agent behind a valid first-party access token that may take calls, else None."""
    if not token:
        return None
    access = AccessToken.objects.select_related("user").filter(token=token).first()
    if access is None or access.is_expired() or access.user is None:
        return None
    if not set(access.scope.split()) & FIRST_PARTY_SCOPES:
        return None  # an identity-only token is no API key (as FirstPartyOAuth2Backend)
    user = access.user
    if gates.blocked_reason(user) is not None or TAKE_CALLS not in resolve_permissions(user):
        return None
    return Agent.objects.filter(user=user, active=True).first()
