"""Symmetric encryption for secrets at rest, with key rotation.

Secrets that must be *used* later (SMTP and directory passwords, SMS gateway
API keys, MFA secrets) cannot be hashed — they are encrypted with
:class:`cryptography.fernet.Fernet` (authenticated AES-128-CBC + HMAC) and only
decrypted at the moment of use.

Keys
----
``ENCRYPTION_KEYS`` is a list of url-safe base64 32-byte keys (``make
encryption-key`` prints one). The **first** key encrypts; **every** key can
decrypt. That is what makes rotation a no-downtime operation:

1. Put the new key first, keep the old one after it. Deploy.
2. Run ``python manage.py rotate_secrets`` — re-encrypts every stored secret
   with the new key.
3. Remove the old key. Deploy.

Without a key
-------------
In development the app still has to run on an empty ``.env``, so with no key
configured values are stored in a reversible, clearly marked passthrough
(``plain:…``). That mode is refused outside ``DEBUG``: production cannot start
without a key (``config/checks.py``), and if one goes missing at runtime
:func:`encrypt_secret` raises rather than store a secret in the clear.
"""

import base64
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

# Marker prefix for the dev passthrough so it is never mistaken for ciphertext.
_PLAIN_PREFIX = "plain:"


def _keys() -> list[str]:
    keys = list(getattr(settings, "ENCRYPTION_KEYS", None) or [])
    single = getattr(settings, "ENCRYPTION_KEY", "") or ""
    if single and single not in keys:
        keys.append(single)
    return [key for key in keys if key]


def _cipher():
    """A MultiFernet over the configured keys, or ``None`` when there are none."""
    keys = _keys()
    if not keys:
        return None
    from cryptography.fernet import Fernet, MultiFernet

    try:
        return MultiFernet([Fernet(key.encode() if isinstance(key, str) else key) for key in keys])
    except Exception as exc:  # noqa: BLE001
        raise ImproperlyConfigured(
            "ENCRYPTION_KEYS contains an invalid key. Each key must be a url-safe "
            "base64-encoded 32-byte value (generate one with `make encryption-key`)."
        ) from exc


def _passthrough_allowed() -> bool:
    return bool(getattr(settings, "DEBUG", False)) or getattr(settings, "ENVIRONMENT", "") == "testing"


def is_encrypted(token: str | None) -> bool:
    """True when ``token`` is real ciphertext (not empty, not the dev passthrough)."""
    return bool(token) and not (token or "").startswith(_PLAIN_PREFIX)


def encrypt_secret(value: str | None) -> str:
    """Encrypt ``value`` for storage; returns "" for empty input."""
    if not value:
        return ""
    cipher = _cipher()
    if cipher is None:
        if not _passthrough_allowed():
            raise ImproperlyConfigured(
                "Cannot store a secret: ENCRYPTION_KEYS is empty. "
                "Generate a key with `make encryption-key` and set it in the environment."
            )
        # Dev fallback: store reversibly but unmistakably un-encrypted.
        return _PLAIN_PREFIX + base64.urlsafe_b64encode(value.encode()).decode()
    return cipher.encrypt(value.encode()).decode()


def decrypt_secret(token: str | None) -> str:
    """Decrypt a value produced by :func:`encrypt_secret`; "" for empty input."""
    if not token:
        return ""
    if token.startswith(_PLAIN_PREFIX):
        return base64.urlsafe_b64decode(token[len(_PLAIN_PREFIX) :].encode()).decode()
    cipher = _cipher()
    if cipher is None:
        # Key removed after encryption — nothing we can do but surface it.
        logger.error("[crypto] cannot decrypt: ENCRYPTION_KEYS is empty")
        return ""
    try:
        return cipher.decrypt(token.encode()).decode()
    except Exception as exc:  # noqa: BLE001
        logger.error("[crypto] failed to decrypt a stored secret: %s", type(exc).__name__)
        return ""


def rotate_secret(token: str | None) -> str:
    """Re-encrypt ``token`` under the current primary key.

    Also upgrades a dev-passthrough value to real ciphertext once a key exists.
    Returns the input unchanged when it cannot be read (so a rotation run never
    destroys a value it failed to decrypt).
    """
    if not token:
        return ""
    cipher = _cipher()
    if cipher is None:
        return token
    if token.startswith(_PLAIN_PREFIX):
        return encrypt_secret(decrypt_secret(token))
    try:
        return cipher.rotate(token.encode()).decode()
    except Exception as exc:  # noqa: BLE001
        logger.error("[crypto] could not rotate a stored secret: %s", type(exc).__name__)
        return token
