"""RFC 6238 TOTP (time-based one-time passwords) using only the standard library.

Kept dependency-free on purpose: the algorithm is small and stable, so there is
no package to keep patched for ~50 lines. :func:`generate_secret` enrols,
:func:`match` checks a code (with a ±1 step window for clock skew) and says
*which* time step it belongs to, and :func:`provisioning_uri` builds the
``otpauth://`` URI the authenticator app scans.

Replay protection
-----------------
A code stays valid for its whole 30-second step (plus the skew window), so
anyone who sees it can use it again within that time. :func:`match` returns the
step the code matched; the caller stores it and refuses any code whose step is
not greater than the last one accepted.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
PERIOD = 30


def generate_secret(num_bytes: int = 20) -> str:
    """A fresh base32 secret (no padding) for a new authenticator enrolment."""
    return base64.b32encode(secrets.token_bytes(num_bytes)).decode().rstrip("=")


def _hotp(secret_b32: str, counter: int) -> str:
    padding = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32.upper() + padding)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**DIGITS)
    return str(code).zfill(DIGITS)


def current_step(at: float | None = None) -> int:
    return int(at if at is not None else time.time()) // PERIOD


def match(secret_b32: str, code: str, *, window: int = 1, at: float | None = None) -> int | None:
    """The time step ``code`` is valid for, or ``None`` when it matches none.

    Every candidate is compared (no early exit) so the time taken does not
    reveal which step, if any, matched.
    """
    if not secret_b32 or not code:
        return None
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return None
    step = current_step(at)
    matched = None
    for drift in range(-window, window + 1):
        try:
            candidate = _hotp(secret_b32, step + drift)
        except (ValueError, TypeError):  # a secret that is not valid base32
            return None
        if hmac.compare_digest(candidate, code):
            matched = step + drift
    return matched


def verify(secret_b32: str, code: str, *, window: int = 1) -> bool:
    """True if ``code`` matches within ±``window`` time steps (clock-skew tolerant)."""
    return match(secret_b32, code, window=window) is not None


def provisioning_uri(secret_b32: str, *, account: str, issuer: str) -> str:
    """The ``otpauth://totp/...`` URI an authenticator app scans as a QR code."""
    label = quote(f"{issuer}:{account}")
    return (
        f"otpauth://totp/{label}?secret={secret_b32}"
        f"&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={PERIOD}"
    )
