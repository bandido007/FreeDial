"""The supervisor page: plain JS, signed in with the starter's OAuth token, like the agent page."""

import json
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.shortcuts import render
from oauth2_provider.models import get_application_model

from apps.agentdesk.views import page_headers


@lru_cache(maxsize=1)
def english_messages():
    return json.loads((Path(__file__).with_name("translations") / "en.json").read_text())


def supervisor_page(request):
    application = get_application_model().objects.filter(name=settings.OAUTH_APP_NAME).first()
    config = {
        "clientId": application.client_id if application else "",
        "tokenUrl": "/oauth2/access_token",
        "revokeUrl": "/oauth2/revoke_token",
        "graphqlUrl": "/graphql",
        "liveUrl": "/ws/supervisor",
        "messages": english_messages(),
    }
    return page_headers(request, render(request, "supervisor/index.html", {"config": config}))
