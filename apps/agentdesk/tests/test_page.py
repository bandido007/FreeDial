"""M3: the agent page is served by Django, with headers that let its phone work and nothing else."""

import json
import re

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import Client

pytestmark = pytest.mark.django_db


def test_the_page_carries_the_sign_in_settings(oauth_app):
    response = Client().get("/agent/")
    assert response.status_code == 200
    page = response.content.decode()
    config = json.loads(re.search(r'id="desk-config" type="application/json">(.*?)</script>', page).group(1))
    assert config["clientId"] == oauth_app.client_id
    assert (config["tokenUrl"], config["graphqlUrl"], config["liveUrl"]) == (
        "/oauth2/access_token",
        "/graphql",
        "/ws/agent",
    )
    assert "jssip-3.12.0.min.js" in page and "agentdesk/app.js" in page


def test_the_microphone_is_allowed_on_this_page_only():
    page = Client().get("/agent/")
    assert "microphone=(self)" in page["Permissions-Policy"]
    assert "microphone=()" in Client().get("/healthz")["Permissions-Policy"], "everywhere else it stays off"


def test_the_page_may_talk_only_to_itself_and_asterisks_sip_websocket():
    policy = Client().get("/agent/", HTTP_HOST="127.0.0.1:8000")["Content-Security-Policy"]
    directives = dict(part.strip().split(" ", 1) for part in policy.split(";"))
    assert directives["script-src"] == "'self'"
    connect = directives["connect-src"].split()
    assert set(connect) == {"'self'", "ws://127.0.0.1:8000", "wss://127.0.0.1:8000", "ws://127.0.0.1:8091"}
    assert directives["frame-ancestors"] == "'none'"
    assert settings.PJSIP_WS_URL.startswith("ws://127.0.0.1:8091")


def test_the_page_scripts_are_there():
    for path in ("agentdesk/app.js", "agentdesk/app.css", "agentdesk/vendor/jssip-3.12.0.min.js"):
        assert finders.find(path), path
