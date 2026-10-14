"""Optional virus scan through a ClamAV daemon.

Off unless ``CLAMAV_ADDRESS`` names a daemon (``host:port``). The content is
streamed over ClamAV's ``INSTREAM`` protocol — nothing is written to disk
first, and no extra package is needed.

When the daemon cannot be reached, ``CLAMAV_FAIL_CLOSED`` decides: refuse the
upload (the default) or accept it marked as unscanned.
"""

import logging
import socket
import struct

from django.conf import settings

from apps.attachments.constants import ScanStatusChoices
from apps.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

INFECTED = 9183
SCANNER_UNAVAILABLE = 9186
CHUNK_BYTES = 64 * 1024
TIMEOUT_SECONDS = 15


def _address() -> tuple[str, int] | None:
    raw = (getattr(settings, "CLAMAV_ADDRESS", "") or "").strip()
    if not raw:
        return None
    host, _, port = raw.rpartition(":")
    try:
        return (host or raw, int(port)) if host else (raw, 3310)
    except ValueError:
        return raw, 3310


def _instream(address: tuple[str, int], data: bytes) -> str:
    with socket.create_connection(address, timeout=TIMEOUT_SECONDS) as connection:
        connection.sendall(b"zINSTREAM\0")
        for start in range(0, len(data), CHUNK_BYTES):
            chunk = data[start : start + CHUNK_BYTES]
            connection.sendall(struct.pack(">I", len(chunk)) + chunk)
        connection.sendall(struct.pack(">I", 0))
        response = b""
        while not response.endswith(b"\0"):
            received = connection.recv(4096)
            if not received:
                break
            response += received
    return response.rstrip(b"\0").decode("utf-8", errors="replace").strip()


def scan(data: bytes) -> str:
    """Scan ``data``; returns a :class:`ScanStatusChoices` value.

    Raises :class:`ValidationError` when the content is infected, or when the
    scanner is unreachable and configured to fail closed.
    """
    address = _address()
    if address is None:
        return ScanStatusChoices.SKIPPED.value
    try:
        verdict = _instream(address, data)
    except OSError as exc:
        logger.error("[scanner] ClamAV unreachable at %s:%s — %s", *address, exc)
        if getattr(settings, "CLAMAV_FAIL_CLOSED", True):
            raise ValidationError(
                "The file could not be checked for viruses. Please try again shortly.",
                code=SCANNER_UNAVAILABLE,
            ) from exc
        return ScanStatusChoices.UNSCANNED.value

    if verdict.endswith("FOUND"):
        logger.warning("[scanner] refused an upload: %s", verdict)
        raise ValidationError("The file was refused by the virus scanner.", code=INFECTED)
    if verdict.endswith("OK"):
        return ScanStatusChoices.CLEAN.value
    logger.error("[scanner] unexpected ClamAV answer: %s", verdict)
    if getattr(settings, "CLAMAV_FAIL_CLOSED", True):
        raise ValidationError(
            "The file could not be checked for viruses. Please try again shortly.", code=SCANNER_UNAVAILABLE
        )
    return ScanStatusChoices.UNSCANNED.value
