import pytest
from django.db import connection

from apps.telephony import asterisk_schema


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """The test database also gets Asterisk's realtime tables, made by Asterisk's own Alembic."""
    with django_db_blocker.unblock():
        asterisk_schema.upgrade(connection.settings_dict)
