"""Production settings.

Everything secret or environment-specific is sourced from real environment
variables (see base.py). The module ends by validating itself: an insecure
value stops the process from starting (see ``config/checks.py``).
"""

import logging

from config.checks import validate_production_settings

from .base import *  # noqa: F401,F403
from .base import (
    AUTHENTICATION_BACKENDS,
    LDAP_SERVER_URI,
    MIDDLEWARE,
    SENTRY_DSN,
    SENTRY_TRACES_SAMPLE_RATE,
    STORAGES,
    build_logging,
    env,
)

logger = logging.getLogger(__name__)

DEBUG = env.bool("DEBUG", default=False)
ENVIRONMENT = env("ENVIRONMENT", default="production")

# In production ALLOWED_HOSTS MUST be set explicitly via the environment.
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

# GraphiQL + introspection only when explicitly enabled.
GRAPHIQL = env.bool("GRAPHIQL", default=False)
EXPOSE_ERROR_DETAILS = env.bool("EXPOSE_ERROR_DETAILS", default=False)

# Serve collected static files efficiently through WhiteNoise.
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
STORAGES = {
    **STORAGES,
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Brute-force protection on.
AXES_ENABLED = env.bool("AXES_ENABLED", default=True)

# ── Security hardening ──────────────────────────────────────────────────
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
# Health probes come from inside the cluster over plain HTTP.
SECURE_REDIRECT_EXEMPT = [r"^healthz$", r"^readyz$"]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
# At least one proxy (the ingress) sits in front of the app.
TRUSTED_PROXY_COUNT = env.int("TRUSTED_PROXY_COUNT", default=1)

# ── Email (real SMTP) ───────────────────────────────────────────────────
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")

# ── Logging: one JSON object per line on stdout ─────────────────────────
LOGGING = build_logging(
    level=env("LOG_LEVEL", default="INFO"),
    fmt=env("LOG_FORMAT", default="json"),
    to_file=env.bool("LOG_TO_FILE", default=False),
)

# ── Error tracking ──────────────────────────────────────────────────────
if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.django import DjangoIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=ENVIRONMENT,
            integrations=[DjangoIntegration(), CeleryIntegration()],
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            # Never ship request bodies, cookies or user details.
            send_default_pii=False,
        )
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; error tracking is off.")

# ── Directory sign-in (LDAP) ────────────────────────────────────────────
# Activated only when LDAP_SERVER_URI is set AND django-auth-ldap is installed
# (requirements/ldap.txt). Directory users then authenticate through the normal
# /oauth2/access_token password grant.
if LDAP_SERVER_URI:
    try:
        import ldap
        from django_auth_ldap.config import LDAPSearch

        AUTH_LDAP_SERVER_URI = LDAP_SERVER_URI
        AUTH_LDAP_BIND_DN = env("LDAP_BIND_DN", default="")
        AUTH_LDAP_BIND_PASSWORD = env("LDAP_BIND_PASSWORD", default="")
        AUTH_LDAP_USER_SEARCH = LDAPSearch(
            env("LDAP_USER_SEARCH_BASE", default=""),
            ldap.SCOPE_SUBTREE,
            env("LDAP_USER_FILTER", default="(mail=%(user)s)"),
        )
        AUTH_LDAP_USER_ATTR_MAP = {
            "first_name": "givenName",
            "last_name": "sn",
            "email": "mail",
        }
        AUTH_LDAP_ALWAYS_UPDATE_USER = True
        AUTH_LDAP_CACHE_TIMEOUT = 3600

        # Insert the LDAP backend before the local ModelBackend so directory
        # credentials are tried first.
        AUTHENTICATION_BACKENDS.insert(
            AUTHENTICATION_BACKENDS.index("django.contrib.auth.backends.ModelBackend"),
            "django_auth_ldap.backend.LDAPBackend",
        )
    except ImportError:
        logger.warning("LDAP_SERVER_URI set but django-auth-ldap is not installed; LDAP disabled.")

# Stored directory configurations (apps.authentication.LdapConfig): registered
# only when python-ldap is importable — without it the backend would be inert.
try:
    import ldap  # noqa: F401

    AUTHENTICATION_BACKENDS.insert(
        AUTHENTICATION_BACKENDS.index("django.contrib.auth.backends.ModelBackend"),
        "apps.authentication.ldap.ConfiguredLdapBackend",
    )
except ImportError:
    logger.info("python-ldap not installed; stored directory configurations are inactive.")

# ── Fail fast ───────────────────────────────────────────────────────────
# Set SKIP_PRODUCTION_CHECKS=1 only for build-time commands that need the
# production settings module but no secrets (e.g. collectstatic on a build box).
if not env.bool("SKIP_PRODUCTION_CHECKS", default=False):
    validate_production_settings(globals())
