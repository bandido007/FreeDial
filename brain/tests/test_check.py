"""`manage.py run_brain --check` tells you plainly what the brain can and cannot reach."""

import socket

import pytest
from django.core.management import CommandError, call_command


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_check_fails_clearly_when_ari_is_unreachable(settings, capsys):
    settings.ARI_URL = f"http://127.0.0.1:{closed_port()}/ari"
    with pytest.raises(CommandError, match="ARI"):
        call_command("run_brain", "--check")
    out = capsys.readouterr().out
    assert "redis" in out.lower() and "ok" in out.lower()
    assert "ari" in out.lower() and "fail" in out.lower()


def test_check_fails_clearly_when_redis_is_unreachable(settings, capsys):
    settings.BUS_REDIS_URL = f"redis://127.0.0.1:{closed_port()}/0"
    settings.ARI_URL = f"http://127.0.0.1:{closed_port()}/ari"
    with pytest.raises(CommandError, match="Redis"):
        call_command("run_brain", "--check")
