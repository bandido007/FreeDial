"""Throwaway key material for development and tests. Never used in production."""

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def ephemeral_rsa_key(key_size: int = 2048) -> str:
    """A fresh PEM-encoded RSA private key (2048 bits keeps keygen fast)."""
    return (
        rsa.generate_private_key(public_exponent=65537, key_size=key_size)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
