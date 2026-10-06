"""Webhook signature verification.

GitHub signs every delivery body with the App's webhook secret
(HMAC-SHA256) and sends it as `X-Hub-Signature-256: sha256=<hex>`. A request
that fails this check must be rejected before its JSON is even parsed.
"""

from __future__ import annotations

import hashlib
import hmac

_PREFIX = "sha256="


def sign_payload(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return _PREFIX + digest


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not secret or not signature_header or not signature_header.startswith(_PREFIX):
        return False
    return hmac.compare_digest(sign_payload(secret, body), signature_header)
