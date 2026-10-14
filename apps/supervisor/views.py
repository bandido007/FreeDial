"""The supervisor page: plain JS, signed in with the starter's OAuth token, like the agent page."""

from django.conf import settings
from django.shortcuts import render
from oauth2_provider.models import get_application_model

from apps.agentdesk.views import page_headers


def supervisor_page(request):
    application = get_application_model().objects.filter(name=settings.OAUTH_APP_NAME).first()
    config = {
        "clientId": application.client_id if application else "",
        "tokenUrl": "/oauth2/access_token",
        "revokeUrl": "/oauth2/revoke_token",
        "graphqlUrl": "/graphql",
        "liveUrl": "/ws/supervisor",
    }
    return page_headers(request, render(request, "supervisor/index.html", {"config": config}))
