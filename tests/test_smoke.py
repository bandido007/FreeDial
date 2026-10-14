"""The project boots, the schema builds, and the conventions hold."""

import pytest
from django.core.management import call_command


def test_system_checks_pass():
    call_command("check")


def test_schema_builds():
    from config.schema import schema

    assert "meQuery" in str(schema)


@pytest.mark.django_db
def test_seed_is_idempotent(settings, capsys):
    settings.DEFAULT_ADMIN_EMAIL = "root@example.org"
    settings.DEFAULT_ADMIN_PASSWORD = "Str0ng!Passw0rd"
    call_command("seed")
    call_command("seed")
    from django.contrib.auth import get_user_model

    assert get_user_model().objects.filter(email="root@example.org").count() == 1
