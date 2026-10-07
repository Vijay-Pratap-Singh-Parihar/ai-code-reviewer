"""Encryption of AI provider credentials at rest.

Each provider's credentials (API key, extra headers) are a small JSON object
encrypted with AES-256-GCM. The key is derived from the deployment secret
(`CREDENTIAL_ENCRYPTION_KEY`) with HKDF, so the raw secret is never used
directly and other uses of it get unrelated keys.

The ciphertext is bound to *where it belongs*: the organisation id, the
provider id and the key version are authenticated as associated data. A
ciphertext copied into another organisation's row, or onto a different
provider, fails to decrypt instead of quietly lending one tenant another
tenant's API key.

Format: `v{key_version}:{base64url(nonce || ciphertext_and_tag)}`. The
version prefix leaves room for key rotation: a new key version decrypts old
rows by version until they are re-encrypted.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

CURRENT_KEY_VERSION = 1
_NONCE_BYTES = 12


class CredentialError(ValueError):
    """Credentials could not be decrypted (wrong key, tampered, or moved)."""


def _derive_key(secret: str, version: int) -> bytes:
    if not secret:
        raise CredentialError("CREDENTIAL_ENCRYPTION_KEY is empty")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=f"revu-ai-provider-credentials-v{version}".encode(),
    ).derive(secret.encode())


def _associated_data(org_id: uuid.UUID, provider_id: uuid.UUID, version: int) -> bytes:
    return f"revu:ai_provider:{org_id}:{provider_id}:v{version}".encode()


def encrypt_credentials(
    secret: str,
    credentials: dict[str, Any],
    *,
    org_id: uuid.UUID,
    provider_id: uuid.UUID,
    version: int = CURRENT_KEY_VERSION,
) -> str:
    nonce = os.urandom(_NONCE_BYTES)
    sealed = AESGCM(_derive_key(secret, version)).encrypt(
        nonce,
        json.dumps(credentials, separators=(",", ":"), sort_keys=True).encode(),
        _associated_data(org_id, provider_id, version),
    )
    return f"v{version}:" + base64.urlsafe_b64encode(nonce + sealed).decode("ascii")


def decrypt_credentials(
    secret: str, token: str, *, org_id: uuid.UUID, provider_id: uuid.UUID
) -> dict[str, Any]:
    try:
        prefix, body = token.split(":", 1)
        version = int(prefix.removeprefix("v"))
        raw = base64.urlsafe_b64decode(body.encode("ascii"))
        plaintext = AESGCM(_derive_key(secret, version)).decrypt(
            raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], _associated_data(org_id, provider_id, version)
        )
    except (ValueError, InvalidTag) as exc:
        raise CredentialError(
            "AI provider credentials can't be decrypted; was CREDENTIAL_ENCRYPTION_KEY "
            "changed, or the row copied from elsewhere? Re-enter the API key."
        ) from exc
    data: dict[str, Any] = json.loads(plaintext)
    return data


def key_hint(api_key: str | None) -> str | None:
    """What the UI may show of a key: a recognisable prefix and the last four
    characters (`sk-ant…a1b2`), never enough to use it."""
    if not api_key:
        return None
    if len(api_key) <= 8:
        return "…" + api_key[-2:]
    prefix = api_key[: api_key.find("-", 0, 8) + 1] if "-" in api_key[:8] else api_key[:3]
    return f"{prefix}…{api_key[-4:]}"
