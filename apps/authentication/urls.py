"""Sign-in routes (mounted under /oauth2/ by config.urls)."""

from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from apps.authentication.views import (
    CustomRevokeTokenView,
    CustomTokenView,
    ExternalSignInView,
    sign_in_providers,
)

app_name = "authentication"

urlpatterns = [
    path("access_token", csrf_exempt(CustomTokenView.as_view()), name="access_token"),
    path("revoke_token", csrf_exempt(CustomRevokeTokenView.as_view()), name="revoke_token"),
    path("providers", sign_in_providers, name="providers"),
    path("external/<slug:provider>", ExternalSignInView.as_view(), name="external_sign_in"),
    # Kept for clients written against the fixed Google route.
    path("google", ExternalSignInView.as_view(provider_key="google"), name="google_sign_in"),
]
