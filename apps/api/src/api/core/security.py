import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from api.core.config import get_settings

settings = get_settings()

ACCESS_TOKEN_TYPE = "access"


class InvalidTokenError(Exception):
    """An access or refresh token is malformed, expired, or unknown."""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str


def create_access_token(*, user_id: uuid.UUID, org_id: uuid.UUID, role: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "org_id": str(org_id),
        "role": role,
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> AccessTokenClaims:
    try:
        payload: dict[str, Any] = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError("invalid or expired access token") from exc

    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise InvalidTokenError("not an access token")

    try:
        return AccessTokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            org_id=uuid.UUID(payload["org_id"]),
            role=payload["role"],
        )
    except (KeyError, ValueError) as exc:
        raise InvalidTokenError("malformed access token payload") from exc


def generate_refresh_token() -> str:
    """A random opaque token — not a JWT. Only its hash is ever stored, so a
    database read alone can't produce a usable credential.
    """
    return secrets.token_urlsafe(48)


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()
