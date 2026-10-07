"""Encryption at rest for GitHub App credentials stored in the database.

Fernet (AES-128-CBC + HMAC-SHA256) keyed from `CREDENTIAL_ENCRYPTION_KEY`.
The configured value is run through SHA-256 to get the 32-byte key Fernet
needs, so a proper `Fernet.generate_key()` value and an arbitrary random
string both work. That derivation is only as strong as the secret itself,
which is why callers refuse the shipped placeholder in production.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

PLACEHOLDER_PREFIX = "change-me"


class CredentialDecryptionError(RuntimeError):
    """Stored ciphertext can't be decrypted, almost always because
    `CREDENTIAL_ENCRYPTION_KEY` changed after the credentials were saved."""


class SecretBox:
    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("CREDENTIAL_ENCRYPTION_KEY is empty")
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise CredentialDecryptionError(
                "stored GitHub App credentials can't be decrypted; was "
                "CREDENTIAL_ENCRYPTION_KEY changed after they were saved?"
            ) from exc


def is_placeholder_secret(secret: str) -> bool:
    return not secret or secret.startswith(PLACEHOLDER_PREFIX)
