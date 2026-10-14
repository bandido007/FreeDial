"""M0: the ground the rest of AlgoDial stands on.

Tests run on PostgreSQL, the local services are reachable and published on
127.0.0.1 only, and the brain is a management command.
"""

import re
from pathlib import Path

import pytest
import redis
from django.conf import settings
from django.core.management import get_commands
from django.db import connection

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.django_db
def test_tests_run_on_postgresql():
    assert connection.vendor == "postgresql"


def test_bus_redis_is_reachable():
    client = redis.Redis.from_url(settings.BUS_REDIS_URL, socket_connect_timeout=2)
    try:
        assert client.ping()
    finally:
        client.close()


def test_compose_publishes_ports_on_localhost_only():
    compose = (ROOT / "docker-compose.yml").read_text()
    ports = re.findall(r'^\s+- "([^"]+:\d+)"', compose, flags=re.MULTILINE)
    assert ports, "no published ports found in docker-compose.yml"
    for mapping in ports:
        assert mapping.startswith("127.0.0.1:"), f"{mapping} is reachable from the network"


def test_run_brain_is_a_management_command():
    assert get_commands().get("run_brain") == "brain"
