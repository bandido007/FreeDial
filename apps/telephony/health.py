"""M4b: how each trunk is doing, for its status in admin and in the Setup guide.

- Reachable: Asterisk checks every trunk's address every 30 s (qualify); ARI says "online" or
  "offline" (GET /endpoints/PJSIP/trunk-<name>).
- Registered (registration trunks): AMI PJSIPShowRegistrationsOutbound, through the AMI user
  deploy/asterisk/m4b-carriers.sh made (127.0.0.1 only, write=reporting). Read-only use: login,
  one action, logoff.

Anything that cannot be asked reads "unknown", never an error on the page.
"""

import socket
from dataclasses import dataclass

import requests
from django.conf import settings

from apps.telephony.models import Trunk, TrunkStyleChoices


@dataclass(frozen=True)
class TrunkHealth:
    reachable: str  # online | offline | unknown
    registration: str | None  # Registered | Unregistered | Rejected | Stopped | unknown; None: an IP trunk

    @property
    def healthy(self) -> bool:
        return self.reachable == "online" and self.registration in (None, "Registered")

    @property
    def label(self) -> str:
        parts = {"online": "Reachable", "offline": "Not reachable", "unknown": "Reachability unknown"}[
            self.reachable
        ]
        if self.registration is not None:
            parts += f", {self.registration.lower() if self.registration != 'unknown' else 'sign-in unknown'}"
        return parts


def reachable(endpoint: str) -> str:
    try:
        response = requests.get(
            f"{settings.ARI_URL}/endpoints/PJSIP/{endpoint}",
            auth=(settings.ARI_USER, settings.ARI_PASSWORD),
            timeout=3,
        )
        if response.status_code == 404:
            return "offline"
        response.raise_for_status()
        return "online" if response.json().get("state") == "online" else "offline"
    except (requests.RequestException, ValueError):
        return "unknown"


def _ami_blocks(text: str) -> list[dict[str, str]]:
    blocks = []
    for raw in text.replace("\r\n", "\n").split("\n\n"):
        block = {}
        for line in raw.splitlines():
            key, sep, value = line.partition(": ")
            if sep:
                block[key.strip()] = value.strip()
        if block:
            blocks.append(block)
    return blocks


def registrations(timeout: float = 3.0) -> dict[str, str] | None:
    """Outbound registration id -> status, over AMI; None if AMI cannot be asked."""
    if not settings.AMI_PASSWORD:
        return None
    host, _, port = settings.AMI_URL.partition(":")
    try:
        with socket.create_connection((host, int(port or 5038)), timeout=timeout) as connection:
            stream = connection.makefile("rwb")
            stream.readline()  # "Asterisk Call Manager/..."
            stream.write(
                (
                    "Action: Login\r\n"
                    f"Username: {settings.AMI_USER}\r\nSecret: {settings.AMI_PASSWORD}\r\nEvents: off\r\n\r\n"
                    "Action: PJSIPShowRegistrationsOutbound\r\nActionID: algodial-health\r\n\r\n"
                    "Action: Logoff\r\n\r\n"
                ).encode()
            )
            stream.flush()
            text = stream.read().decode(errors="replace")  # until Asterisk closes after Logoff
    except (OSError, ValueError):
        return None
    blocks = _ami_blocks(text)
    if any(
        block.get("Response") == "Error" and "Authentication" in block.get("Message", "") for block in blocks
    ):
        return None
    return {
        block["ObjectName"]: block.get("Status", "unknown")
        for block in blocks
        if block.get("Event") == "OutboundRegistrationDetail" and "ObjectName" in block
    }


def trunk_health() -> dict[str, TrunkHealth]:
    """Every live trunk's health, by name."""
    live = list(Trunk.objects.filter(active=True))
    signed_in = registrations() if any(t.style == TrunkStyleChoices.REGISTRATION for t in live) else {}
    return {
        trunk.name: TrunkHealth(
            reachable=reachable(trunk.endpoint),
            registration=None
            if trunk.style != TrunkStyleChoices.REGISTRATION
            else ("unknown" if signed_in is None else signed_in.get(trunk.endpoint, "Unregistered")),
        )
        for trunk in live
    }
