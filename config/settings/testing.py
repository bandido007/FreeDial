"""Test settings — fast, isolated, no external services required."""

from config.keys import ephemeral_rsa_key

from .base import *  # noqa: F401,F403
from .base import OAUTH2_PROVIDER, build_logging, env

DEBUG = False
ENVIRONMENT = "testing"

ALLOWED_HOSTS = ["*"]

# In-memory SQLite by default, so `make test` needs nothing running. CI sets
# TEST_DATABASE_URL to its PostgreSQL service to test against the real engine.
# project: AlgoDial tests run on PostgreSQL (PJSIP realtime tables are PostgreSQL tables).
DATABASES = {
    "default": env.db("TEST_DATABASE_URL", default="postgres://postgres:postgres@127.0.0.1:5442/algodial")
}

# Fast, throwaway password hashing for the test database.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Run Celery tasks synchronously instead of dispatching to a broker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# In-memory cache so tests don't need a running Redis.
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Never lock accounts out during tests (the lockout test overrides this).
AXES_ENABLED = False

# No outbound HTTPS in tests — skip the breached-password (HIBP) lookup.
PWNED_PASSWORD_CHECK = False

# Deterministic regardless of the developer's .env.
ALLOW_SELF_REGISTRATION = True
EXPOSE_ERROR_DETAILS = False
NEW_DEVICE_LOGIN_ALERT = False
HEALTH_CHECK_BROKER = False
TRUSTED_PROXY_COUNT = 0
MFA_REQUIRED_ACCOUNT_TYPES = []
MFA_REQUIRED_ROLES = []
PASSWORD_HISTORY_COUNT = 5
PASSWORD_MAX_AGE_DAYS = 0
ENCRYPTION_KEYS = ["kZcJ0y7hYw3m0l6b8lq6wq1n2m3o4p5q6r7s8t9u0vA="]
OIDC_PROVIDERS = {}
STORAGE_BACKEND = "local"
CLAMAV_ADDRESS = ""

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# The suite exercises the full SMS path without a gateway; the production
# refusal is covered by an explicit override_settings test. The gateway values
# are blanked so a developer's .env never turns the LOG tests into real sends.
SMS_ALLOW_LOG_PROVIDER = True
SMS_API_URL = ""
SMS_API_KEY = ""
SMS_SENDER_ID = ""
SMS_SYSTEM_ID = ""
SMS_SERVICE_CODE = ""

LOGGING = build_logging(level="WARNING", fmt="text", to_file=False)


# OIDC provider tests need a signing key; mint a throwaway one in-memory so the
# suite is hermetic (no env var, no key file).
OAUTH2_PROVIDER = {
    **OAUTH2_PROVIDER,
    "ALLOWED_REDIRECT_URI_SCHEMES": ["https"],
    "OIDC_ENABLED": True,
    "OIDC_RSA_PRIVATE_KEY": ephemeral_rsa_key(),
    "OIDC_ISS_ENDPOINT": "http://testserver",
}

# ── project ── AlgoDial: tests use their own Redis database, never the lab's streams.
BUS_REDIS_URL = env("TEST_BUS_REDIS_URL", default="redis://127.0.0.1:6392/5")
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
