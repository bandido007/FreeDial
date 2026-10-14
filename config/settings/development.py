"""Local development settings."""

from .base import *  # noqa: F401,F403
from .base import INSTALLED_APPS, MIDDLEWARE, OAUTH2_PROVIDER, build_logging, env

DEBUG = True
ENVIRONMENT = env("ENVIRONMENT", default="development")

ALLOWED_HOSTS = ["*"]

# django-debug-toolbar — only sensible while DEBUG is on.
INSTALLED_APPS += ["debug_toolbar"]
MIDDLEWARE.insert(
    MIDDLEWARE.index("django.middleware.common.CommonMiddleware") + 1,
    "debug_toolbar.middleware.DebugToolbarMiddleware",
)
INTERNAL_IPS = ["127.0.0.1", "localhost"]

# Lockouts get in the way during local iteration.
AXES_ENABLED = env.bool("AXES_ENABLED", default=False)

# Show the real exception to the developer.
EXPOSE_ERROR_DETAILS = env.bool("EXPOSE_ERROR_DETAILS", default=True)

# No outbound lookup while iterating locally.
PWNED_PASSWORD_CHECK = env.bool("PWNED_PASSWORD_CHECK", default=False)

# Public sign-up on, so the whole register → verify → login journey can be
# exercised without editing .env first.
ALLOW_SELF_REGISTRATION = env.bool("ALLOW_SELF_REGISTRATION", default=True)

# E-mail: the console by default; set EMAIL_HOST=localhost EMAIL_PORT=1025 and
# EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend to use the Mailpit
# container from docker-compose (inbox at http://localhost:8025).
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
SMS_ALLOW_LOG_PROVIDER = env.bool("SMS_ALLOW_LOG_PROVIDER", default=True)

# Run Celery tasks inline instead of dispatching to a broker, so local dev needs
# neither a running worker nor a result backend. `EAGER_PROPAGATES=False` keeps
# a notification-delivery hiccup from surfacing in the request that triggered
# it. Set CELERY_TASK_ALWAYS_EAGER=0 to exercise a real worker locally.
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=True)
CELERY_TASK_EAGER_PROPAGATES = False

# The web process runs tasks inline, so there may be no broker to reach.
HEALTH_CHECK_BROKER = env.bool("HEALTH_CHECK_BROKER", default=False)

# python-ldap is an optional system dependency; simulate the directory so the
# "pull users" flow works out of the box (override with LDAP_SIMULATE_DIRECTORY=0).
LDAP_SIMULATE_DIRECTORY = env.bool("LDAP_SIMULATE_DIRECTORY", default=True)

# Human-readable logs, also written to logs/<slug>.log.
LOGGING = build_logging(
    level=env("LOG_LEVEL", default="INFO"),
    fmt=env("LOG_FORMAT", default="text"),
    to_file=env.bool("LOG_TO_FILE", default=True),
)

# OAuth2 in development: allow http redirect URIs (base.py computed the list
# before DEBUG was forced on here).
OAUTH2_PROVIDER = {
    **OAUTH2_PROVIDER,
    "ALLOWED_REDIRECT_URI_SCHEMES": env.list("OAUTH_ALLOWED_REDIRECT_SCHEMES", default=["http", "https"]),
}
