"""Health endpoints, security headers, rate limits, secrets at rest, status codes."""

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, RequestFactory

from apps.core import checks, crypto, health, ratelimit
from apps.core.exceptions import ThrottledError
from apps.core.graphql import status as status_catalogue
from apps.core.utils import Utils, camel_to_snake, generate_uuid7, get_client_ip, strip_mount_prefix


# ── Health ───────────────────────────────────────────────────────────────────
def test_liveness_touches_nothing():
    response = Client().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_readiness_checks_the_dependencies():
    response = Client().get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "cache": "ok"}}


@pytest.mark.django_db
def test_readiness_answers_503_and_hides_the_reason(monkeypatch):
    monkeypatch.setattr(health, "_check_cache", lambda: health.FAILED)
    response = Client().get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["cache"] == "failed"
    assert response.json()["status"] == "unavailable"


def test_health_endpoints_only_answer_get():
    assert Client().post("/healthz").status_code == 405


@pytest.mark.django_db
def test_a_broken_database_or_broker_is_reported(monkeypatch, settings):
    class Broken:
        def cursor(self):
            raise RuntimeError("down")

    monkeypatch.setattr(health, "connection", Broken())
    assert health._check_database() == health.FAILED
    settings.CELERY_BROKER_URL = "redis://127.0.0.1:1/0"
    assert health._check_broker() in (health.OK, health.FAILED)


# ── Security headers ─────────────────────────────────────────────────────────
def test_api_responses_get_the_strict_policy():
    response = Client().get("/healthz")
    assert "default-src 'none'" in response["Content-Security-Policy"]
    assert "camera=()" in response["Permissions-Policy"]
    assert response["X-Content-Type-Options"] == "nosniff"


@pytest.mark.django_db
def test_html_surfaces_get_a_policy_their_assets_can_run_under():
    response = Client().get("/admin/login/")
    assert "default-src 'self'" in response["Content-Security-Policy"]


# ── Rate limiting ────────────────────────────────────────────────────────────
def test_throttle_raises_once_the_limit_is_passed():
    for _ in range(3):
        ratelimit.throttle("test", "198.51.100.7", limit=3, window=60)
    with pytest.raises(ThrottledError) as excinfo:
        ratelimit.throttle("test", "198.51.100.7", limit=3, window=60)
    assert excinfo.value.code == 9018
    ratelimit.reset("test", "198.51.100.7")
    assert ratelimit.throttle("test", "198.51.100.7", limit=3, window=60) == 1


def test_a_blank_identifier_is_not_counted():
    assert ratelimit.throttle("test", "", limit=1, window=60) == 0
    assert ratelimit.throttle("test", None, limit=1, window=60) == 0


def test_named_scopes_read_their_numbers_from_settings(settings):
    settings.RATE_LIMITS = {"quick": (1, 60)}
    assert ratelimit.limit_for("quick") == (1, 60)
    assert ratelimit.limit_for("unknown") == (30, 60)
    ratelimit.throttle_scope("quick", "someone")
    with pytest.raises(ThrottledError):
        ratelimit.throttle_scope("quick", "someone")


# ── Client address ───────────────────────────────────────────────────────────
def test_forwarded_header_is_ignored_without_a_trusted_proxy(settings):
    settings.TRUSTED_PROXY_COUNT = 0
    request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="203.0.113.9", REMOTE_ADDR="10.0.0.1")
    assert get_client_ip(request) == "10.0.0.1"


def test_the_address_is_counted_from_the_right(settings):
    settings.TRUSTED_PROXY_COUNT = 1
    spoofed = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="1.1.1.1, 203.0.113.9", REMOTE_ADDR="10.0.0.1")
    assert get_client_ip(spoofed) == "203.0.113.9"
    settings.TRUSTED_PROXY_COUNT = 2
    assert get_client_ip(spoofed) == "1.1.1.1"
    assert get_client_ip(RequestFactory().get("/", REMOTE_ADDR="10.0.0.1")) == "10.0.0.1"
    assert get_client_ip(None) is None


# ── Secrets at rest ──────────────────────────────────────────────────────────
def test_secrets_round_trip(settings):
    settings.ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
    token = crypto.encrypt_secret("s3cret")
    assert token != "s3cret" and crypto.is_encrypted(token)
    assert crypto.decrypt_secret(token) == "s3cret"
    assert crypto.encrypt_secret("") == "" and crypto.decrypt_secret(None) == ""


def test_keys_rotate_without_losing_a_secret(settings):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    settings.ENCRYPTION_KEYS = [old]
    token = crypto.encrypt_secret("s3cret")

    settings.ENCRYPTION_KEYS = [new, old]  # new key first, old one still readable
    assert crypto.decrypt_secret(token) == "s3cret"
    rotated = crypto.rotate_secret(token)

    settings.ENCRYPTION_KEYS = [new]  # the old key is retired
    assert crypto.decrypt_secret(rotated) == "s3cret"
    assert crypto.decrypt_secret(token) == ""


def test_without_a_key_development_falls_back_and_production_refuses(settings):
    settings.ENCRYPTION_KEYS, settings.ENCRYPTION_KEY = [], ""
    token = crypto.encrypt_secret("s3cret")
    assert token.startswith("plain:") and not crypto.is_encrypted(token)
    assert crypto.decrypt_secret(token) == "s3cret"

    settings.ENVIRONMENT, settings.DEBUG = "production", False
    with pytest.raises(ImproperlyConfigured):
        crypto.encrypt_secret("s3cret")


def test_a_passthrough_value_is_upgraded_once_a_key_exists(settings):
    settings.ENCRYPTION_KEYS, settings.ENCRYPTION_KEY = [], ""
    plain = crypto.encrypt_secret("s3cret")
    settings.ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
    upgraded = crypto.rotate_secret(plain)
    assert crypto.is_encrypted(upgraded)
    assert crypto.decrypt_secret(upgraded) == "s3cret"


def test_an_invalid_key_is_a_configuration_error(settings):
    settings.ENCRYPTION_KEYS = ["not-a-key"]
    with pytest.raises(ImproperlyConfigured):
        crypto.encrypt_secret("s3cret")


# ── Status codes ─────────────────────────────────────────────────────────────
def test_the_catalogue_is_assembled_from_every_app():
    assert status_catalogue.get_status(9001)["status"] is True  # core
    assert status_catalogue.get_status(8000)["status"] is True  # authentication
    assert status_catalogue.get_status(9140)["status"] is False  # rbac
    assert status_catalogue.get_status(123456)["messageEn"] == "Unknown status code"


def test_messages_follow_the_content_language():
    class Info:
        def __init__(self, language):
            self.context = RequestFactory().post("/graphql", HTTP_CONTENT_LANGUAGE=language)

    assert status_catalogue.resolve_message(9003, Info("en")) == "Record not found"
    assert status_catalogue.resolve_message(9003, Info("sw")) == "Taarifa haijapatikana"
    assert status_catalogue.resolve_message(9003, Info("fr")) == "Record not found"
    assert status_catalogue.resolve_message(9003, Info("sw"), "Custom") == "Custom"


def test_missing_fields_are_named():
    class Form:
        title = ""
        body = "text"

    assert Utils.empty_and_null_checker(Form(), ["title", "body"]) is False
    assert status_catalogue.resolve_message(9005) == "Missing required field(s): title"
    assert Utils.empty_and_null_checker(Form(), ["body"]) is True


def test_the_shipped_catalogue_is_valid():
    assert checks.check_status_codes(None) == []


def test_collisions_and_missing_translations_are_reported(monkeypatch):
    broken = [
        ("core", {"code": 9001, "status": True, "messageEn": "ok", "messageSw": "sawa"}),
        ("rbac", {"code": 9001, "status": True, "messageEn": "ok", "messageSw": "sawa"}),
        ("users", {"code": 9500, "status": False, "messageEn": "only english"}),
        ("users", {"code": "text", "status": False}),
    ]
    monkeypatch.setattr(status_catalogue, "load_catalogue", lambda: broken)
    found = {error.id for error in checks.check_status_codes(None)}
    assert found == {"starter.E001", "starter.E002", "starter.E004"}


# ── Small helpers ────────────────────────────────────────────────────────────
def test_helpers():
    assert generate_uuid7().version == 7
    assert generate_uuid7() != generate_uuid7()
    assert camel_to_snake("createdAt") == "created_at"
    assert camel_to_snake("user.firstName") == "user__first_name"
    assert strip_mount_prefix("/backend/graphql") == "/graphql"
    assert strip_mount_prefix("/graphql") == "/graphql"
    assert len(Utils.generate_otp()) == 6 and Utils.generate_otp().isdigit()
    assert Utils.generate_reference("INV").startswith("INV-")
    assert Utils.normalize_phone("+255 (755) 123-456") == "+255755123456"
    assert Utils.string_to_bool("Yes") and not Utils.string_to_bool("no")


def test_the_login_identifier_reads_either_spelling():
    from django.test import RequestFactory

    from apps.core.utils import login_identifier

    assert login_identifier(None, {"username": " Asha@Example.org ", "password": "x"}) == "asha@example.org"
    assert login_identifier(None, {"email": "asha@example.org"}) == "asha@example.org"
    request = RequestFactory().post("/oauth2/access_token", {"username": "Posted@example.org"})
    assert login_identifier(request, {}) == "posted@example.org"
    assert login_identifier(RequestFactory().post("/x", {}), None) is None


@pytest.mark.django_db
def test_rotate_secrets_re_encrypts_every_stored_secret(settings, user):
    from io import StringIO

    from cryptography.fernet import Fernet
    from django.core.management import call_command

    from apps.authentication.models import LdapConfig
    from apps.core import crypto

    old_key = Fernet.generate_key().decode()
    settings.ENCRYPTION_KEYS = [old_key]
    config = LdapConfig.objects.create(name="Main", ldap_url="ldap://dir", bind_dn="cn=svc")
    config.set_bind_password("s3cret")
    config.save()
    before = config.bind_password_encrypted

    new_key = Fernet.generate_key().decode()
    settings.ENCRYPTION_KEYS = [new_key, old_key]  # the new key first, the old one still readable
    out = StringIO()
    call_command("rotate_secrets", stdout=out)
    config.refresh_from_db()
    assert config.bind_password_encrypted != before and "Rotated" in out.getvalue()

    settings.ENCRYPTION_KEYS = [new_key]  # the old key is dropped
    assert config.get_bind_password() == "s3cret"
    assert crypto.decrypt_secret(before) == ""  # the old ciphertext is unreadable now, as intended
