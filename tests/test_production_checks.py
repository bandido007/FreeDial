"""Production refuses to start on an unsafe setting."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.checks import production_problems, validate_production_settings

SAFE = {
    "SECRET_KEY": "k" * 60,
    "INSECURE_SECRET_KEY": "django-insecure-CHANGE-ME-IN-PRODUCTION",
    "DEBUG": False,
    "ALLOWED_HOSTS": ["api.example.org"],
    "ENCRYPTION_KEYS": ["a-key"],
    "DATABASES": {"default": {"ENGINE": "django.db.backends.postgresql", "PASSWORD": "long-and-random"}},
    "CORS_ALLOWED_ORIGINS": ["https://portal.example.org"],
    "EXPOSE_ERROR_DETAILS": False,
    "LDAP_SIMULATE_DIRECTORY": False,
    "SMS_ALLOW_LOG_PROVIDER": False,
    "AXES_ENABLED": True,
}


def test_safe_settings_pass():
    assert production_problems(SAFE) == []
    validate_production_settings(SAFE)


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"SECRET_KEY": "django-insecure-CHANGE-ME-IN-PRODUCTION"}, "SECRET_KEY is the insecure default"),
        ({"SECRET_KEY": "short"}, "shorter than"),
        ({"DEBUG": True}, "DEBUG is on"),
        ({"ALLOWED_HOSTS": []}, "ALLOWED_HOSTS is empty"),
        ({"ALLOWED_HOSTS": ["*"]}, "contains '*'"),
        ({"ENCRYPTION_KEYS": []}, "ENCRYPTION_KEYS is empty"),
        ({"DATABASES": {"default": {"ENGINE": "postgresql", "PASSWORD": "postgres"}}}, "default password"),
        ({"CORS_ALLOWED_ORIGINS": []}, "CORS_ALLOWED_ORIGINS is empty"),
        ({"CORS_ALLOWED_ORIGINS": ["http://localhost:4200"]}, "localhost"),
        ({"EXPOSE_ERROR_DETAILS": True}, "EXPOSE_ERROR_DETAILS"),
        ({"LDAP_SIMULATE_DIRECTORY": True}, "LDAP_SIMULATE_DIRECTORY"),
        ({"SMS_ALLOW_LOG_PROVIDER": True}, "SMS_ALLOW_LOG_PROVIDER"),
        ({"AXES_ENABLED": False}, "AXES_ENABLED"),
        ({"ENABLE_SSO_PROVIDER": True, "OIDC_RSA_PRIVATE_KEY": ""}, "OIDC_RSA_PRIVATE_KEY"),
        ({"GRAPHQL_BATCH_ENABLED": True, "GRAPHQL_MAX_BATCH_SIZE": 0}, "GRAPHQL_MAX_BATCH_SIZE"),
    ],
)
def test_each_unsafe_setting_is_named(change, expected):
    problems = production_problems({**SAFE, **change})
    assert len(problems) == 1
    assert expected in problems[0]


def test_every_problem_is_listed_at_once():
    with pytest.raises(ImproperlyConfigured) as excinfo:
        validate_production_settings({**SAFE, "DEBUG": True, "ALLOWED_HOSTS": ["*"], "ENCRYPTION_KEYS": []})
    message = str(excinfo.value)
    assert "3 unsafe production setting(s)" in message
    assert "1." in message and "3." in message
