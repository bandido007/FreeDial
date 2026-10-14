"""
Base settings shared by every environment.

Configuration is read with **django-environ**: real OS environment variables
take precedence, then a local ``.env`` file (handy for development), and every
lookup has a sane default so a missing key degrades gracefully instead of
raising ``KeyError`` at import time. Production is the exception: it refuses to
start on an insecure default (see ``config/checks.py``).

Environment selection
----------------------
``DJANGO_SETTINGS_MODULE`` decides which concrete module is loaded:

* ``config.settings.development`` (default for manage.py)
* ``config.settings.production``
* ``config.settings.testing``

Each of those does ``from .base import *`` and overrides only what differs.
"""

from datetime import timedelta
from pathlib import Path

import environ

from config.schedule import build_beat_schedule

# ── Paths ────────────────────────────────────────────────────────────────
# BASE_DIR -> the project root (three parents up: base.py -> settings/ ->
# config/ -> <root>).
BASE_DIR = Path(__file__).resolve().parent.parent.parent
APPS_DIR = BASE_DIR / "apps"

# ── Environment ────────────────────────────────────────────────────────────
INSECURE_SECRET_KEY = "django-insecure-CHANGE-ME-IN-PRODUCTION"

env = environ.Env(
    DEBUG=(bool, False),
    SECRET_KEY=(str, INSECURE_SECRET_KEY),
    ALLOWED_HOSTS=(list, ["*"]),
    GRAPHIQL=(bool, True),
    SCRIPT_NAME=(str, ""),
)

# Read .env if it exists. Real environment variables still win, so this is safe
# to keep enabled in production (where vars are typically injected directly).
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

# ── Project identity ───────────────────────────────────────────────────────
# Everything a person reads (admin title, e-mail brand, authenticator issuer)
# derives from these, so a new project is rebranded from the environment.
PROJECT_NAME = env("PROJECT_NAME", default="AlgoDial")
PROJECT_SLUG = env("PROJECT_SLUG", default="algodial")
# Free-form deployment label stamped on log lines and error reports.
ENVIRONMENT = env("ENVIRONMENT", default="development")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
SCRIPT_NAME = env("SCRIPT_NAME")
FORCE_SCRIPT_NAME = SCRIPT_NAME or None

# Toggle the GraphiQL IDE + introspection. Disabled in production.
GRAPHIQL = env("GRAPHIQL")

# The SPA's public origin — used to build links in e-mails.
FRONTEND_DOMAIN = env("FRONTEND_DOMAIN", default="http://localhost:4200")

# Every route is also served under this path prefix, for deployments that
# publish the API on the frontend's host (https://portal.example/backend/…).
# Empty = the routes are served at the root only.
API_MOUNT_PREFIX = env("API_MOUNT_PREFIX", default="backend").strip("/")

# ── Optional modules ───────────────────────────────────────────────────────
# `scripts/init_project.py` deletes the folders of modules a project does not
# want; these switches cover the ones that stay in the repo but are turned off.

# ── Applications ─────────────────────────────────────────────────────────
DJANGO_APPS = [
    "daphne",  # project: first, so `runserver` serves HTTP and WebSockets (ASGI)
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "graphene_django",
    "corsheaders",
    "rest_framework",
    "oauth2_provider",
    "axes",
    "django_celery_beat",
    "channels",  # project: the live WebSocket (apps.realtime)
]

# Foundation apps live under the apps/ package. Each AppConfig sets an explicit
# label, so models are referenced as e.g. 'users.User'.
FOUNDATION_APPS = [
    "apps.core",
    "apps.rbac",
    "apps.users",
    "apps.notifications",
    "apps.authentication",
    "apps.audit",
    "apps.attachments",
    "apps.configuration",
]

OPTIONAL_APPS: list[str] = []

# Your business apps. `make app name=<name>` adds a line here.
PROJECT_APPS: list[str] = [
    "brain",  # project: call control, lives in brain/ (not apps/)
    "aiworker",  # project: the AI agent's voice worker (M8), lives in aiworker/
    "apps.telephony",
    "apps.teams",
    "apps.calls",
    "apps.agents",
    "apps.queues",
    "apps.ai",
    "apps.realtime",
    "apps.agentdesk",
    "apps.supervisor",
    # <scaffold:project-apps>
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + FOUNDATION_APPS + OPTIONAL_APPS + PROJECT_APPS

# ── Middleware ─────────────────────────────────────────────────────────────
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Accepts or mints the X-Request-ID that every log line and audit row of
    # this request carries. First, so even an early rejection is traceable.
    "apps.core.middleware.RequestIdMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Resolves the bearer token into request.user for OAuth2-authenticated calls.
    "oauth2_provider.middleware.OAuth2TokenMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Content-Security-Policy + Permissions-Policy response headers.
    "apps.core.middleware.SecurityHeadersMiddleware",
    # Exposes the authenticated user to the ORM layer (BaseModel.save / delete)
    # so created_by / updated_by are stamped centrally.
    "apps.core.middleware.CurrentUserMiddleware",
    # Endpoint transaction logging (apps.audit) — records every tracked call
    # with duration + severity; runs after the user is resolved so logs are
    # attributed, and before axes so it observes the final response.
    "apps.audit.middleware.RequestAuditMiddleware",
    # django-axes must be the LAST middleware so it sees the final request.user.
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ── Templates ──────────────────────────────────────────────────────────────
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ── Database ─────────────────────────────────────────────────────────────
# DATABASE_URL example: postgres://USER:PASSWORD@HOST:PORT/NAME
DEFAULT_DATABASE_URL = f"postgres://postgres:postgres@127.0.0.1:5442/{PROJECT_SLUG}"  # project
DATABASES = {"default": env.db("DATABASE_URL", default=DEFAULT_DATABASE_URL)}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── Authentication ─────────────────────────────────────────────────────────
AUTH_USER_MODEL = "users.User"

AUTHENTICATION_BACKENDS = [
    # AxesStandaloneBackend must be first so lockouts are enforced before any
    # credential check runs.
    "axes.backends.AxesStandaloneBackend",
    # Scope-aware DOT backend: only first-party (read/write) tokens authenticate
    # API requests — an external system's identity-only token never does.
    "apps.authentication.backends.FirstPartyOAuth2Backend",
    "django.contrib.auth.backends.ModelBackend",
]

# ── Password policy ────────────────────────────────────────────────────────
# Defaults; administrators can tighten them at runtime from the System
# settings screen (apps.configuration), which takes precedence when set.
PASSWORD_MIN_LENGTH = env.int("PASSWORD_MIN_LENGTH", default=8)
# Reject a new password equal to any of the last N. 0 turns history off.
PASSWORD_HISTORY_COUNT = env.int("PASSWORD_HISTORY_COUNT", default=5)
# Force a change after N days. 0 means passwords never expire.
PASSWORD_MAX_AGE_DAYS = env.int("PASSWORD_MAX_AGE_DAYS", default=0)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    # Length + upper/lower/digit/special; reads PASSWORD_MIN_LENGTH at call time.
    {"NAME": "apps.core.validators.StrongPasswordValidator"},
    # Reject breached passwords via HIBP k-anonymity (fail-open; gated by the flag).
    {"NAME": "apps.authentication.password_validators.PwnedPasswordValidator"},
    # Reject reuse of recent passwords and record each accepted change.
    {"NAME": "apps.authentication.password_validators.PasswordHistoryValidator"},
]
# Enable the HIBP breached-password lookup (needs outbound HTTPS). Off in tests.
PWNED_PASSWORD_CHECK = env.bool("PWNED_PASSWORD_CHECK", default=not DEBUG)

# ── Multi-factor authentication policy ─────────────────────────────────────
# MFA is opt-in per user. Accounts matching either list below MUST enrol
# before they can use the API (login answers 8015 until they do).
MFA_REQUIRED_ACCOUNT_TYPES = env.list("MFA_REQUIRED_ACCOUNT_TYPES", default=[])
MFA_REQUIRED_ROLES = env.list("MFA_REQUIRED_ROLES", default=[])

# ── Registration ───────────────────────────────────────────────────────────
# Public self sign-up. Off by default: most internal systems create accounts
# from the admin screens (invite) or a directory. Turn on for public portals.
ALLOW_SELF_REGISTRATION = env.bool("ALLOW_SELF_REGISTRATION", default=False)
# What a person must have filled in before `User.is_profile_complete` is true.
PROFILE_REQUIRED_FIELDS = env.list("PROFILE_REQUIRED_FIELDS", default=["first_name", "last_name", "phone"])
# Country calling code assumed for a bare local mobile number.
DEFAULT_PHONE_COUNTRY_CODE = env("DEFAULT_PHONE_COUNTRY_CODE", default="255")

# ── Internationalisation ─────────────────────────────────────────────────
LANGUAGE_CODE = "en-us"
# Languages the API answers in (status messages, localized fields). The first
# is the fallback. The `Content-Language` request header selects one.
API_LANGUAGES = env.list("API_LANGUAGES", default=["en", "sw"])
TIME_ZONE = env("TIME_ZONE", default="Africa/Dar_es_Salaam")
USE_I18N = True
USE_TZ = True

# ── Static & media ─────────────────────────────────────────────────────────
STATIC_URL = env("STATIC_URL", default="/static/")
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = env("MEDIA_URL", default="/media/")
MEDIA_ROOT = BASE_DIR / "media"

# ── File storage ───────────────────────────────────────────────────────────
# "local" keeps files under MEDIA_ROOT. "s3" targets any S3-compatible store
# (AWS S3, MinIO) through django-storages (in requirements/production.txt).
STORAGE_BACKEND = env("STORAGE_BACKEND", default="local")
STORAGES: dict[str, dict] = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
if STORAGE_BACKEND == "s3":
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("S3_BUCKET_NAME", default=""),
            "endpoint_url": env("S3_ENDPOINT_URL", default="") or None,
            "access_key": env("S3_ACCESS_KEY", default="") or None,
            "secret_key": env("S3_SECRET_KEY", default="") or None,
            "region_name": env("S3_REGION", default="") or None,
            # Private by default: downloads go through the access-checked
            # endpoint or a short-lived signed URL, never a public object URL.
            "default_acl": "private",
            "querystring_auth": True,
            "querystring_expire": env.int("S3_SIGNED_URL_TTL_SECONDS", default=300),
            "file_overwrite": False,
        },
    }

# Upload limits and the optional virus scan (see apps.attachments).
ATTACHMENT_MAX_IMAGE_MB = env.int("ATTACHMENT_MAX_IMAGE_MB", default=5)
ATTACHMENT_MAX_DOCUMENT_MB = env.int("ATTACHMENT_MAX_DOCUMENT_MB", default=15)
# Media sub-folders that may be read WITHOUT authentication.
ATTACHMENT_PUBLIC_BUCKETS = env.list("ATTACHMENT_PUBLIC_BUCKETS", default=["public"])
# ClamAV daemon address, e.g. "clamav:3310". Empty = scanning off.
CLAMAV_ADDRESS = env("CLAMAV_ADDRESS", default="")
# When the scanner is configured but unreachable: refuse the upload (True) or
# accept it unscanned (False).
CLAMAV_FAIL_CLOSED = env.bool("CLAMAV_FAIL_CLOSED", default=True)

DATA_UPLOAD_MAX_MEMORY_SIZE = env.int("DATA_UPLOAD_MAX_MB", default=25) * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # larger multipart files spool to disk

# ── GraphQL (graphene) ─────────────────────────────────────────────────────
GRAPHENE = {
    "SCHEMA": "config.schema.schema",
    # A session that must first change its password or set up two-factor is
    # refused everything except what it needs to comply.
    "MIDDLEWARE": ["apps.authentication.middleware.RestrictedSessionMiddleware"],
}
# Guard rails enforced by apps.core.graphql.views.HardenedGraphQLView before a
# query is executed. A request that exceeds one is rejected with code 9002.
GRAPHQL_MAX_DEPTH = env.int("GRAPHQL_MAX_DEPTH", default=10)
# Total number of fields selected, after fragments are expanded.
GRAPHQL_MAX_COMPLEXITY = env.int("GRAPHQL_MAX_COMPLEXITY", default=500)
# Aliased fields per request (stops one request fanning out into hundreds of
# copies of the same expensive field, e.g. a password-guessing batch).
GRAPHQL_MAX_ALIASES = env.int("GRAPHQL_MAX_ALIASES", default=15)
# Top-level fields per operation.
GRAPHQL_MAX_ROOT_FIELDS = env.int("GRAPHQL_MAX_ROOT_FIELDS", default=10)
# Array-batched requests are refused unless enabled, and then capped.
GRAPHQL_BATCH_ENABLED = env.bool("GRAPHQL_BATCH_ENABLED", default=False)
GRAPHQL_MAX_BATCH_SIZE = env.int("GRAPHQL_MAX_BATCH_SIZE", default=10)
# Ceiling on `itemsPerPage` for every pageable query.
GRAPHQL_MAX_PAGE_SIZE = env.int("GRAPHQL_MAX_PAGE_SIZE", default=500)
# Send exception text to the client on an unexpected error. Development only:
# in production the client gets a generic message and the request ID.
EXPOSE_ERROR_DETAILS = env.bool("EXPOSE_ERROR_DETAILS", default=DEBUG)

# ── OAuth2 provider ────────────────────────────────────────────────────────
# The project's own SPA logs in via the password grant (first-party, scopes
# read/write). With the optional SSO module enabled, the backend is also an
# OpenID Connect identity provider for external systems (scopes openid /
# profile / email, authorization-code + PKCE).
OAUTH_APP_NAME = env("OAUTH_APP_NAME", default=f"{PROJECT_NAME} Web")
OIDC_RSA_PRIVATE_KEY = env.str("OIDC_RSA_PRIVATE_KEY", multiline=True, default="")
# DOT's Application model is swappable; pinning the (default) reference lets
# other apps FK it the way AUTH_USER_MODEL is FK'd. Never change post-migration.
OAUTH2_PROVIDER_APPLICATION_MODEL = "oauth2_provider.Application"
OAUTH2_PROVIDER = {
    "ACCESS_TOKEN_EXPIRE_SECONDS": env.int("ACCESS_TOKEN_EXPIRE_SECONDS", default=3600),
    "REFRESH_TOKEN_EXPIRE_SECONDS": env.int("REFRESH_TOKEN_EXPIRE_SECONDS", default=86400),
    "ROTATE_REFRESH_TOKEN": True,
    "SCOPES": {"read": "Read scope", "write": "Write scope"},
    # Without this pin DEFAULT_SCOPES falls back to __all__, silently widening
    # every first-party token to any scope added later.
    "DEFAULT_SCOPES": ["read", "write"],
    "PKCE_REQUIRED": True,
    "ALLOWED_REDIRECT_URI_SCHEMES": env.list(
        "OAUTH_ALLOWED_REDIRECT_SCHEMES", default=(["http", "https"] if DEBUG else ["https"])
    ),
    # Account gates are re-checked on every bearer request, and only the
    # first-party application is granted scopes (default-deny).
    "OAUTH2_VALIDATOR_CLASS": "apps.authentication.oauth_validator.FirstPartyOAuth2Validator",
    "SCOPES_BACKEND_CLASS": "apps.authentication.scopes.FirstPartyScopes",
}


# ── External sign-in (this backend as an OIDC *client*) ────────────────────
# "Continue with Google / Microsoft / Keycloak / another system". Each entry
# names an OpenID Connect issuer whose ID tokens this backend accepts:
#   {"google": {"label": "Google", "issuer": "https://accounts.google.com",
#               "client_id": "…apps.googleusercontent.com"}}
# Set OIDC_PROVIDERS to that JSON. GOOGLE_OAUTH_CLIENT_ID is a shortcut that
# registers the "google" entry on its own.
OIDC_PROVIDERS = env.json("OIDC_PROVIDERS", default={})
GOOGLE_OAUTH_CLIENT_ID = env("GOOGLE_OAUTH_CLIENT_ID", default="")
if GOOGLE_OAUTH_CLIENT_ID and "google" not in OIDC_PROVIDERS:
    OIDC_PROVIDERS["google"] = {
        "label": "Google",
        "issuer": "https://accounts.google.com",
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
    }
# Create an account on first external sign-in. When off, only people who
# already have an account (matched by verified e-mail) may sign in this way.
OIDC_AUTO_PROVISION = env.bool("OIDC_AUTO_PROVISION", default=True)

# ── Django REST framework ────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("oauth2_provider.contrib.rest_framework.OAuth2Authentication",),
    # Default-deny for identity-only tokens: every DRF view is first-party
    # (read/write scope) unless it explicitly opts out. New endpoints are safe
    # by default.
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
        "oauth2_provider.contrib.rest_framework.TokenHasReadWriteScope",
        "apps.authentication.middleware.UnrestrictedSession",
    ),
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
}

# ── Caching (Redis) ────────────────────────────────────────────────────────
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://127.0.0.1:6392/1"),  # project
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        "KEY_PREFIX": PROJECT_SLUG,
    }
}
# How long a user's resolved permission set stays cached. Role and permission
# changes invalidate it immediately; this is only the safety net.
RBAC_PERMISSION_CACHE_SECONDS = env.int("RBAC_PERMISSION_CACHE_SECONDS", default=300)

# ── Celery ─────────────────────────────────────────────────────────────────
# Broker uses Redis DB 0, the Django cache DB 1, task results DB 2.
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://127.0.0.1:6392/0")  # project
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://127.0.0.1:6392/2")  # project
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_CREATE_MISSING_QUEUES = True
CELERY_TASK_TIME_LIMIT = env.int("CELERY_TASK_TIME_LIMIT", default=600)
CELERY_TASK_SOFT_TIME_LIMIT = env.int("CELERY_TASK_SOFT_TIME_LIMIT", default=540)
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
# Database-backed periodic schedule, editable from the admin.
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
# Composed from each app's schedule.py — see config/schedule.py.
CELERY_BEAT_SCHEDULE = build_beat_schedule(INSTALLED_APPS)

# ── E-mail ───────────────────────────────────────────────────────────────────
# The notifications EmailConfiguration (DB) is the source of truth at runtime;
# these are the fallback used when no configuration row exists yet. In
# development the console backend prints e-mails to the log.
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default=f"{PROJECT_NAME} <no-reply@{PROJECT_SLUG}.local>")

# Every outgoing mail renders inside the branded shell
# (templates/emails/base_email.html); these fill its header and footer.
EMAIL_BRAND = {
    "organisation": env("EMAIL_BRAND_ORGANISATION", default=""),
    "portal": env("EMAIL_BRAND_PORTAL", default=PROJECT_NAME),
    "tagline": env("EMAIL_BRAND_TAGLINE", default=""),
    "logo_url": env("EMAIL_BRAND_LOGO_URL", default=""),
    "portal_url": FRONTEND_DOMAIN,
    "website": env("EMAIL_BRAND_WEBSITE", default=""),
    "support_email": env("EMAIL_BRAND_SUPPORT_EMAIL", default=""),
    "address": env("EMAIL_BRAND_ADDRESS", default=""),
    "accent": env("EMAIL_BRAND_ACCENT", default="#0f5c50"),
    "from_name": env("EMAIL_BRAND_FROM_NAME", default=PROJECT_NAME),
}

# ── Secrets at rest ──────────────────────────────────────────────────────────
# Fernet keys for encrypting DB-stored secrets (SMTP / LDAP passwords, SMS API
# keys, MFA secrets). Generate one with `make secret-key`.
#
# ENCRYPTION_KEYS is a comma-separated list: the FIRST key encrypts, every key
# can decrypt. To rotate, put the new key first, keep the old one after it,
# deploy, run `manage.py rotate_secrets`, then drop the old key.
# ENCRYPTION_KEY (single key) is still honoured.
ENCRYPTION_KEYS = env.list("ENCRYPTION_KEYS", default=[])
ENCRYPTION_KEY = env("ENCRYPTION_KEY", default="")
if ENCRYPTION_KEY and ENCRYPTION_KEY not in ENCRYPTION_KEYS:
    ENCRYPTION_KEYS = [*ENCRYPTION_KEYS, ENCRYPTION_KEY]

# ── Directory / LDAP (optional) ──────────────────────────────────────────────
# Needs requirements/ldap.txt. With LDAP_SIMULATE_DIRECTORY on, "pull users"
# returns a small deterministic set so the flow can be exercised without a
# directory server. Development only.
LDAP_SERVER_URI = env("LDAP_SERVER_URI", default="")
LDAP_SIMULATE_DIRECTORY = env.bool("LDAP_SIMULATE_DIRECTORY", default=False)
# Account type stamped on people who sign in through a directory.
LDAP_ACCOUNT_TYPE = env("LDAP_ACCOUNT_TYPE", default="Internal")
# Role granted to a directory account the first time it is seen. Empty = none.
LDAP_DEFAULT_ROLE = env("LDAP_DEFAULT_ROLE", default="")

# ── Notifications & OTP tunables ─────────────────────────────────────────────
OTP_TTL_MINUTES = env.int("OTP_TTL_MINUTES", default=10)
OTP_MAX_ATTEMPTS = env.int("OTP_MAX_ATTEMPTS", default=5)
OTP_RESEND_COOLDOWN_SECONDS = env.int("OTP_RESEND_COOLDOWN_SECONDS", default=45)
OTP_MAX_PER_DAY = env.int("OTP_MAX_PER_DAY", default=8)
OTP_RETENTION_DAYS = env.int("OTP_RETENTION_DAYS", default=3)
NOTIFICATION_MAX_ATTEMPTS = env.int("NOTIFICATION_MAX_ATTEMPTS", default=5)
# A never-verified self-registration older than this (days) is purged.
UNVERIFIED_ACCOUNT_TTL_DAYS = env.int("UNVERIFIED_ACCOUNT_TTL_DAYS", default=7)
# An invitation (set-password link for an admin-created account) stays valid
# this long.
INVITE_TTL_HOURS = env.int("INVITE_TTL_HOURS", default=72)
# E-mail the account holder when a sign-in comes from a device not seen before.
NEW_DEVICE_LOGIN_ALERT = env.bool("NEW_DEVICE_LOGIN_ALERT", default=True)

# SMS: with no gateway configured the LOG provider writes the payload to the
# log. That may only *count as delivered* in development — anywhere else a
# missing gateway must fail loudly rather than claim a code was sent.
SMS_ALLOW_LOG_PROVIDER = env.bool("SMS_ALLOW_LOG_PROVIDER", default=DEBUG)
# eGA mGov SMS gateway — the .env half of the SMS configuration. The admin
# screen's SmsConfiguration row wins per field; blanks fall back to these.
SMS_API_URL = env("SMS_API_URL", default="")
SMS_API_KEY = env("SMS_API_KEY", default="")
SMS_SENDER_ID = env("SMS_SENDER_ID", default="")
SMS_SYSTEM_ID = env("SMS_SYSTEM_ID", default="")
SMS_SERVICE_CODE = env("SMS_SERVICE_CODE", default="")

# ── Rate limits (cache-backed; see apps.core.ratelimit) ───────────────────────
# scope -> (limit, window-seconds). Applied per IP and, where noted in the
# calling service, per e-mail as well.
RATE_LIMITS = {
    "login": (env.int("RL_LOGIN_LIMIT", default=30), env.int("RL_LOGIN_WINDOW", default=300)),
    "register": (env.int("RL_REGISTER_LIMIT", default=5), env.int("RL_REGISTER_WINDOW", default=300)),
    "otp": (env.int("RL_OTP_LIMIT", default=6), env.int("RL_OTP_WINDOW", default=600)),
    "password-reset": (env.int("RL_RESET_LIMIT", default=5), env.int("RL_RESET_WINDOW", default=900)),
    "external-sign-in": (
        env.int("RL_EXTERNAL_LIMIT", default=20),
        env.int("RL_EXTERNAL_WINDOW", default=300),
    ),
    "public-file": (
        env.int("RL_PUBLIC_FILE_LIMIT", default=120),
        env.int("RL_PUBLIC_FILE_WINDOW", default=60),
    ),
}
# Number of reverse proxies in front of the app. 0 = trust only REMOTE_ADDR;
# N = take the client address N hops from the right of X-Forwarded-For.
TRUSTED_PROXY_COUNT = env.int("TRUSTED_PROXY_COUNT", default=0)
# Extra disposable-email domains to block, beyond the built-in list.
DISPOSABLE_EMAIL_DOMAINS = env.list("DISPOSABLE_EMAIL_DOMAINS", default=[])

# ── Legal consent (versioned; bump to force re-acceptance) ────────────────────
CONSENT_TERMS_VERSION = env("CONSENT_TERMS_VERSION", default="2026-01")
CONSENT_PRIVACY_VERSION = env("CONSENT_PRIVACY_VERSION", default="2026-01")

# ── Audit / endpoint-log tunables ────────────────────────────────────────────
# URL prefixes whose requests are written to the endpoint log.
AUDIT_TRACKED_PREFIXES = env.list("AUDIT_TRACKED_PREFIXES", default=["/graphql", "/oauth2/", "/files/"])
# Persist successful (INFO) transactions too — turn off to log failures only.
AUDIT_PERSIST_SUCCESS_REQUESTS = env.bool("AUDIT_PERSIST_SUCCESS_REQUESTS", default=True)
# Retention horizons (days) for the request-log purge task, by severity.
# Unresolved ERROR rows are never purged — they represent open defects.
AUDIT_INFO_RETENTION_DAYS = env.int("AUDIT_INFO_RETENTION_DAYS", default=14)
AUDIT_WARNING_RETENTION_DAYS = env.int("AUDIT_WARNING_RETENTION_DAYS", default=60)
AUDIT_RESOLVED_ERROR_RETENTION_DAYS = env.int("AUDIT_RESOLVED_ERROR_RETENTION_DAYS", default=180)
# Model-change trail retention (days). 0 keeps the trail for ever.
AUDIT_TRAIL_RETENTION_DAYS = env.int("AUDIT_TRAIL_RETENTION_DAYS", default=0)

# ── Password reset (emailed link) ────────────────────────────────────────────
# The link is FRONTEND_DOMAIN + PASSWORD_RESET_PATH + "/<token>".
PASSWORD_RESET_TTL_MINUTES = env.int("PASSWORD_RESET_TTL_MINUTES", default=30)
PASSWORD_RESET_PATH = env("PASSWORD_RESET_PATH", default="/auth/set-password")

# ── django-axes (brute-force protection) ──────────────────────────────────
AXES_CACHE = "default"
# Two independent defences, each with the threshold that suits it:
#   * the ACCOUNT is locked after AXES_FAILURE_LIMIT wrong passwords, wherever
#     the guesses come from — rotating addresses or user agents does not help;
#   * the ADDRESS is slowed down by the "login" rate limit below, with a much
#     higher ceiling, because one address is often a whole office.
# Locking by address at the account threshold would let one person's typos
# lock out everyone behind the same network.
AXES_LOCKOUT_PARAMETERS = env.list("AXES_LOCKOUT_PARAMETERS", default=["username"])
# axes warns (W006) whenever the address is not part of the lockout key; the
# address is covered by the rate limit instead.
SILENCED_SYSTEM_CHECKS = ["axes.W006"]
# Behind a proxy the socket address is the proxy's: use the same
# trusted-proxy-aware resolution as the rest of the project.
AXES_CLIENT_IP_CALLABLE = "apps.core.utils.get_client_ip"
# The password grant signs in with a ``username`` credential while the user
# model's USERNAME_FIELD is ``email``; axes must read both, normalised.
AXES_USERNAME_CALLABLE = "apps.core.utils.login_identifier"
AXES_LOCK_OUT_AT_FAILURE = env.bool("AXES_LOCK_OUT_AT_FAILURE", default=True)
AXES_RESET_ON_SUCCESS = True
AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", default=5)
AXES_COOLOFF_TIME = timedelta(minutes=env.int("AXES_COOLOFF_MINUTES", default=15))

# ── CORS / CSRF ────────────────────────────────────────────────────────────
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:4200", "http://127.0.0.1:4200"],
)
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=["http://localhost:4200", "http://127.0.0.1:4200"],
)
CORS_ALLOW_METHODS = ["GET", "OPTIONS", "PATCH", "POST", "PUT", "DELETE"]
CORS_ALLOW_HEADERS = (
    "accept",
    "authorization",
    "content-type",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
    "x-request-id",
    "content-language",
    "platform",
)
CORS_EXPOSE_HEADERS = ("x-request-id",)

# ── Security headers ───────────────────────────────────────────────────────
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
# This is an API: it serves JSON, so the strictest policy applies everywhere…
CONTENT_SECURITY_POLICY = env(
    "CONTENT_SECURITY_POLICY",
    default="default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
)
# …except the two HTML surfaces, which need their own scripts and styles.
CONTENT_SECURITY_POLICY_HTML = env(
    "CONTENT_SECURITY_POLICY_HTML",
    default=(
        "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data:; "
        "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    ),
)
CONTENT_SECURITY_POLICY_HTML_PREFIXES = ["/admin/", "/graphql", "/__debug__/"]
PERMISSIONS_POLICY = env(
    "PERMISSIONS_POLICY",
    default="accelerometer=(), camera=(), geolocation=(), gyroscope=(), microphone=(), payment=(), usb=()",
)

# ── Health checks ──────────────────────────────────────────────────────────
# /readyz always checks the database and the cache. The broker check opens a
# connection to Redis/RabbitMQ; turn it off where the web pod has no broker.
HEALTH_CHECK_BROKER = env.bool("HEALTH_CHECK_BROKER", default=True)

# ── Bootstrap administrator (consumed by `manage.py sync_rbac`) ───────────────
DEFAULT_ADMIN_EMAIL = env("DEFAULT_ADMIN_EMAIL", default="")
DEFAULT_ADMIN_PASSWORD = env("DEFAULT_ADMIN_PASSWORD", default="")
DEFAULT_ADMIN_FIRST_NAME = env("DEFAULT_ADMIN_FIRST_NAME", default="System")
DEFAULT_ADMIN_LAST_NAME = env("DEFAULT_ADMIN_LAST_NAME", default="Administrator")
# Pin the first-party OAuth2 client ID (otherwise generated on first sync).
OAUTH_CLIENT_ID = env("OAUTH_CLIENT_ID", default="")

# ── Logging ────────────────────────────────────────────────────────────────
# Every record carries the request ID. Text format for people (development),
# one JSON object per line for log collectors (production).
LOG_LEVEL = env("LOG_LEVEL", default="INFO")
LOG_FORMAT = env("LOG_FORMAT", default="text")  # "text" | "json"
LOG_TO_FILE = env.bool("LOG_TO_FILE", default=False)
LOG_DIR = BASE_DIR / "logs"


def build_logging(*, level: str, fmt: str, to_file: bool) -> dict:
    """The LOGGING dict for the given format — shared by every environment."""
    handlers: dict[str, dict] = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": fmt,
            "filters": ["request_context"],
        },
    }
    if to_file:
        LOG_DIR.mkdir(exist_ok=True)
        handlers["file"] = {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": str(LOG_DIR / f"{PROJECT_SLUG}.log"),
            "when": "midnight",
            "backupCount": 14,
            "formatter": fmt,
            "filters": ["request_context"],
            "encoding": "utf-8",
        }
    active = list(handlers)
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"request_context": {"()": "apps.core.logs.RequestContextFilter"}},
        "formatters": {
            "text": {"format": "%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s: %(message)s"},
            "json": {"()": "apps.core.logs.JsonFormatter"},
        },
        "handlers": handlers,
        "root": {"handlers": active, "level": level},
        "loggers": {
            "django": {"handlers": active, "level": level, "propagate": False},
            # One line per request is the endpoint log's job, not runserver's.
            "django.server": {"handlers": active, "level": "WARNING", "propagate": False},
        },
    }


LOGGING = build_logging(level=LOG_LEVEL, fmt=LOG_FORMAT, to_file=LOG_TO_FILE)

# ── Error tracking (optional) ──────────────────────────────────────────────
# Active only when SENTRY_DSN is set and sentry-sdk is installed.
SENTRY_DSN = env("SENTRY_DSN", default="")
SENTRY_TRACES_SAMPLE_RATE = env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0)

# ── project ── AlgoDial ───────────────────────────────────────────────────
# Everything below is AlgoDial's own; starter updates never touch it.
# Call control: the brain reaches Asterisk over ARI (REST + WebSocket).
ARI_URL = env("ARI_URL", default="http://127.0.0.1:8091/ari").rstrip("/")
ARI_USER = env("ARI_USER", default="algodial")
ARI_PASSWORD = env("ARI_PASSWORD", default="")
ARI_APP = env("ARI_APP", default="algodial")

# The bus between web, brain and writer: Redis Streams (docs: CLAUDE.md, "Messaging").
BUS_REDIS_URL = env("BUS_REDIS_URL", default="redis://127.0.0.1:6392/4")
BUS_COMMANDS_STREAM = env("BUS_COMMANDS_STREAM", default="algodial:commands")
BUS_EVENTS_STREAM = env("BUS_EVENTS_STREAM", default="algodial:events")
# Live agent state: one Redis hash the brain writes (agent uid -> JSON) and the web reads.
BUS_AGENTS_KEY = env("BUS_AGENTS_KEY", default="algodial:agents")
# Live calls (supervisor pages): a hash the brain writes, and a Pub/Sub channel announcing each change.
BUS_CALLS_KEY = env("BUS_CALLS_KEY", default="algodial:calls")
BUS_LIVE_CHANNEL = env("BUS_LIVE_CHANNEL", default="algodial:live")
# The brain's name in the commands consumer group; keep it stable so a restart re-reads its own pending.
BRAIN_CONSUMER = env("BRAIN_CONSUMER", default="brain-1")
# The password every demo-centre user gets (manage.py seed_demo); generated into .env when empty.
DEMO_PASSWORD = env("DEMO_PASSWORD", default="")

# The AI agent (M8): OpenAI for speech-to-text, the language model and text-to-speech. The worker
# (manage.py run_ai) reaches the call's audio over Asterisk's WebSocket media (ARI externalMedia).
OPENAI_API_KEY = env("OPENAI_API_KEY", default="")
AI_CONSUMER = env("AI_CONSUMER", default="ai-1")  # the worker's name in the events consumer group "ai"

# PJSIP realtime: a SIP account saved here is rows Asterisk reads live (apps.telephony).
# The tables come from Asterisk's own Alembic scripts in its source tree.
ASTERISK_SRC_DIR = env("ASTERISK_SRC_DIR", default=str(Path.home() / "asterisk-22"))
PJSIP_REALM = env("PJSIP_REALM", default="asterisk")
PJSIP_DEFAULT_CONTEXT = env("PJSIP_DEFAULT_CONTEXT", default="lab")
PJSIP_TRANSPORT_SOFTPHONE = env("PJSIP_TRANSPORT_SOFTPHONE", default="transport-udp")
PJSIP_TRANSPORT_BROWSER = env("PJSIP_TRANSPORT_BROWSER", default="transport-ws")
# Where the agent page's browser phone reaches Asterisk (SIP over WebSocket, the HTTP server's /ws).
# ws:// is enough on 127.0.0.1 (a secure context); another machine needs wss:// and HTTPS.
PJSIP_WS_URL = env("PJSIP_WS_URL", default="ws://127.0.0.1:8091/ws")
PJSIP_DOMAIN = env("PJSIP_DOMAIN", default="127.0.0.1")
# How the brain dials a number an agent transfers to. Internal phones here: PJSIP/{number};
# with a SIP trunk: PJSIP/{number}@<trunk endpoint>.
TRANSFER_DIAL_TEMPLATE = env(
    "TRANSFER_DIAL_TEMPLATE", default="PJSIP/{number}"
)  # the domain in the browser phone's SIP URI
# The read-only database role Asterisk logs in with (res_pgsql.conf); `manage.py pjsip_schema` creates it.
ASTERISK_DB_USER = env("ASTERISK_DB_USER", default="asterisk")
ASTERISK_DB_PASSWORD = env("ASTERISK_DB_PASSWORD", default="")

# Live: the agent page's WebSocket (apps.realtime) goes through the Channels layer on Redis.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [BUS_REDIS_URL], "prefix": "algodial:live"},
    }
}
