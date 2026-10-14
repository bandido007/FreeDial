"""Production fail-fast checks.

A development default that reaches production is a silent vulnerability: the
server starts, works, and is insecure. ``validate_production_settings`` is
called at the end of ``config/settings/production.py`` and raises
``ImproperlyConfigured`` listing **every** problem at once, so a deployment is
fixed in one pass instead of one restart per mistake.

Pure function over a dict of settings — no Django imports beyond the
exception — so it is trivially testable.
"""

from django.core.exceptions import ImproperlyConfigured

MIN_SECRET_KEY_LENGTH = 50


def production_problems(settings: dict) -> list[str]:
    """Every reason these settings are unsafe to run in production."""
    problems: list[str] = []

    secret = settings.get("SECRET_KEY") or ""
    if secret == settings.get("INSECURE_SECRET_KEY") or secret.startswith("django-insecure"):
        problems.append("SECRET_KEY is the insecure default. Generate one with `make secret-key`.")
    elif len(secret) < MIN_SECRET_KEY_LENGTH:
        problems.append(f"SECRET_KEY is shorter than {MIN_SECRET_KEY_LENGTH} characters.")

    if settings.get("DEBUG"):
        problems.append("DEBUG is on. It exposes source code and settings in error pages.")

    hosts = settings.get("ALLOWED_HOSTS") or []
    if not hosts:
        problems.append("ALLOWED_HOSTS is empty. List the host names this backend answers to.")
    elif "*" in hosts:
        problems.append("ALLOWED_HOSTS contains '*'. List the exact host names instead.")

    if not settings.get("ENCRYPTION_KEYS"):
        problems.append(
            "ENCRYPTION_KEYS is empty. Stored secrets (SMTP, LDAP, SMS, MFA) would be "
            "kept unencrypted. Generate a key with `make encryption-key`."
        )

    database = (settings.get("DATABASES") or {}).get("default", {})
    if database.get("PASSWORD") in ("", "postgres", None) and "sqlite" not in database.get("ENGINE", ""):
        problems.append("DATABASE_URL uses an empty or default password.")

    if not settings.get("CORS_ALLOWED_ORIGINS"):
        problems.append("CORS_ALLOWED_ORIGINS is empty. The frontend would be refused.")

    if any(
        "localhost" in origin or "127.0.0.1" in origin
        for origin in settings.get("CORS_ALLOWED_ORIGINS") or []
    ):
        problems.append("CORS_ALLOWED_ORIGINS still lists a localhost origin.")

    if settings.get("GRAPHQL_BATCH_ENABLED") and not settings.get("GRAPHQL_MAX_BATCH_SIZE"):
        problems.append("GRAPHQL_BATCH_ENABLED is on without a GRAPHQL_MAX_BATCH_SIZE.")

    if settings.get("EXPOSE_ERROR_DETAILS"):
        problems.append("EXPOSE_ERROR_DETAILS is on. Exception text would be sent to clients.")

    if settings.get("LDAP_SIMULATE_DIRECTORY"):
        problems.append("LDAP_SIMULATE_DIRECTORY is on. It fabricates directory users.")

    if settings.get("SMS_ALLOW_LOG_PROVIDER"):
        problems.append("SMS_ALLOW_LOG_PROVIDER is on. Text messages would be logged, not sent.")

    if settings.get("AXES_ENABLED") is False:
        problems.append("AXES_ENABLED is off. Login brute-force protection is disabled.")

    if settings.get("ENABLE_SSO_PROVIDER") and not settings.get("OIDC_RSA_PRIVATE_KEY"):
        problems.append("ENABLE_SSO_PROVIDER is on but OIDC_RSA_PRIVATE_KEY is empty.")

    return problems


def validate_production_settings(settings: dict) -> None:
    """Raise ``ImproperlyConfigured`` when any production problem exists."""
    problems = production_problems(settings)
    if problems:
        lines = "\n".join(f"  {index}. {problem}" for index, problem in enumerate(problems, start=1))
        raise ImproperlyConfigured(
            f"Refusing to start: {len(problems)} unsafe production setting(s).\n{lines}\n"
            "Fix them in the environment (see .env.example), then start again."
        )
