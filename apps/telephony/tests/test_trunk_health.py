"""M4b: a trunk's health, from ARI (reachable) and AMI (registered). A fake AMI here; the lab check
reads the real one."""

import socket
import threading

import pytest

from apps.telephony import health, trunks

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_reload(monkeypatch):
    monkeypatch.setattr(trunks, "reload_asterisk", lambda **kwargs: None)


@pytest.fixture
def fake_ami(settings):
    """Answers one session like Asterisk's AMI: a banner, then whatever ``reply`` holds, then closes."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    state = {"reply": "", "asked": b""}

    def serve():
        connection, _ = server.accept()
        connection.sendall(b"Asterisk Call Manager/9.0.0\r\n")
        while b"Logoff" not in state["asked"]:
            state["asked"] += connection.recv(4096)
        connection.sendall(state["reply"].encode())
        connection.close()

    threading.Thread(target=serve, daemon=True).start()
    settings.AMI_URL = f"127.0.0.1:{server.getsockname()[1]}"
    settings.AMI_USER, settings.AMI_PASSWORD = "algodial-health", "secret"
    yield state
    server.close()


REPLY = (
    "Response: Success\r\nMessage: Authentication accepted\r\n\r\n"
    "Response: Success\r\nEventList: start\r\nActionID: algodial-health\r\n\r\n"
    "Event: OutboundRegistrationDetail\r\nObjectName: trunk-voip\r\nStatus: Registered\r\n\r\n"
    "Event: OutboundRegistrationDetail\r\nObjectName: trunk-other\r\nStatus: Rejected\r\n\r\n"
    "Event: OutboundRegistrationDetailComplete\r\nEventList: Complete\r\n\r\n"
    "Response: Goodbye\r\n\r\n"
)


def test_registrations_come_from_ami_with_a_read_only_session(fake_ami):
    fake_ami["reply"] = REPLY
    assert health.registrations() == {"trunk-voip": "Registered", "trunk-other": "Rejected"}
    asked = fake_ami["asked"].decode()
    assert (
        "Action: Login" in asked
        and "Action: PJSIPShowRegistrationsOutbound" in asked
        and "Action: Logoff" in asked
    )
    assert "Originate" not in asked and "Command" not in asked


def test_a_wrong_ami_password_reads_unknown(fake_ami):
    fake_ami["reply"] = "Response: Error\r\nMessage: Authentication failed\r\n\r\n"
    assert health.registrations() is None


def test_each_trunk_says_if_it_is_reachable_and_registered(fake_ami, monkeypatch):
    fake_ami["reply"] = REPLY
    trunks.save_trunk(actor=None, name="lab-carrier", preset="lab-sipp")
    trunks.save_trunk(
        actor=None,
        name="voip",
        style="registration",
        hosts="127.0.0.3",
        username="u1",
        password="Carrier-secret-123",
    )
    monkeypatch.setattr(
        health, "reachable", lambda endpoint: {"trunk-lab-carrier": "online"}.get(endpoint, "offline")
    )
    states = health.trunk_health()
    assert states["lab-carrier"].healthy and states["lab-carrier"].label == "Reachable"
    assert (states["voip"].registration, states["voip"].healthy) == ("Registered", False)
    assert states["voip"].label == "Not reachable, registered"
