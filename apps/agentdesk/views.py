"""The agent page: one HTML page with plain JS, signed in with the starter's OAuth token.

The page itself is public (it is the sign-in form too); everything it shows comes
from the API and the live WebSocket after sign-in. It sends its own security
headers: the starter's default Permissions-Policy blocks the microphone.
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.shortcuts import render
from oauth2_provider.models import get_application_model


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def agent_page(request):
    application = get_application_model().objects.filter(name=settings.OAUTH_APP_NAME).first()
    config = {
        "clientId": application.client_id if application else "",
        "tokenUrl": "/oauth2/access_token",
        "revokeUrl": "/oauth2/revoke_token",
        "graphqlUrl": "/graphql",
        "liveUrl": "/ws/agent",
    }
    return page_headers(request, render(request, "agentdesk/index.html", {"config": config}))


def page_headers(request, response):
    """CSP for a desk page: its own scripts, its WebSockets and Asterisk's SIP socket; the microphone allowed."""
    host = request.get_host()
    connect = " ".join(
        filter(None, ["'self'", f"ws://{host}", f"wss://{host}", _origin(settings.PJSIP_WS_URL)])
    )
    response["Content-Security-Policy"] = "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data:",
            f"connect-src {connect}",
            "media-src 'self' blob: mediastream:",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'self'",
        ]
    )
    response["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=(), payment=(), usb=()"
    return response
