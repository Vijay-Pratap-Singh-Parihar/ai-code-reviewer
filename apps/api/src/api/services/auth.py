import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from db.audit import AuditAction, record_audit
from db.auth import RefreshToken
from db.organization import Organization, User, UserRole
from db.tenancy import bind_org
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)

settings = get_settings()


class EmailAlreadyRegisteredError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class RefreshTokenInvalidError(Exception):
    """Unknown, expired, or already-revoked refresh token."""


class RefreshTokenReusedError(Exception):
    """A revoked (already-rotated) refresh token was presented again.

    This is the signal that a refresh token was stolen: the legitimate
    client already rotated past it, so whoever presented it again has a
    stale copy. The caller must revoke the rest of that token's chain.
    """


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    refresh_expires_at: datetime


async def _issue_token_pair(session: AsyncSession, user: User) -> tuple[TokenPair, RefreshToken]:
    access_token = create_access_token(user_id=user.id, org_id=user.org_id, role=user.role.value)
    raw_refresh = generate_refresh_token()
    expires_at = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_token_expire_days)

    refresh_row = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(raw_refresh),
        expires_at=expires_at,
    )
    session.add(refresh_row)
    await session.flush()

    return TokenPair(access_token, raw_refresh, expires_at), refresh_row


async def _sync_platform_admin(session: AsyncSession, user: User) -> None:
    """Grant platform admin to emails listed in REVU_PLATFORM_ADMIN_EMAILS.
    One-way on purpose: removing an email from the list does not silently
    demote anyone; revoking is an explicit, audited CLI action."""
    if user.is_platform_admin or user.email.lower() not in get_settings().platform_admin_emails:
        return
    user.is_platform_admin = True
    await bind_org(session, user.org_id)
    record_audit(
        session,
        org_id=user.org_id,
        actor_id=user.id,
        action=AuditAction.PLATFORM_ADMIN_GRANTED,
        target=f"user:{user.email}",
        metadata={"source": "REVU_PLATFORM_ADMIN_EMAILS"},
    )


async def signup(
    session: AsyncSession, *, org_name: str, email: str, password: str
) -> tuple[User, TokenPair]:
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise EmailAlreadyRegisteredError(email)

    org = Organization(name=org_name)
    session.add(org)
    await session.flush()

    user = User(
        org_id=org.id,
        email=email,
        password_hash=hash_password(password),
        role=UserRole.OWNER,
    )
    session.add(user)
    await session.flush()
    await _sync_platform_admin(session, user)

    tokens, _ = await _issue_token_pair(session, user)
    await session.commit()
    return user, tokens


async def authenticate(
    session: AsyncSession, *, email: str, password: str
) -> tuple[User, TokenPair]:
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        raise InvalidCredentialsError(email)
    # Login events belong to the user's organisation; the audit log is under
    # row-level security, so act for it before recording.
    await bind_org(session, user.org_id)
    if not verify_password(password, user.password_hash):
        record_audit(
            session,
            org_id=user.org_id,
            actor_id=user.id,
            action=AuditAction.AUTH_LOGIN_FAILED,
            target=f"user:{user.email}",
        )
        await session.commit()
        raise InvalidCredentialsError(email)

    await _sync_platform_admin(session, user)
    record_audit(
        session,
        org_id=user.org_id,
        actor_id=user.id,
        action=AuditAction.AUTH_LOGIN,
        target=f"user:{user.email}",
    )
    tokens, _ = await _issue_token_pair(session, user)
    await session.commit()
    return user, tokens


async def rotate_refresh_token(
    session: AsyncSession, raw_refresh_token: str
) -> tuple[User, TokenPair]:
    token_hash = hash_refresh_token(raw_refresh_token)
    current = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )

    if current is None:
        raise RefreshTokenInvalidError("unknown refresh token")

    if current.revoked_at is not None:
        await _revoke_all_for_user(session, current.user_id)
        raise RefreshTokenReusedError(str(current.id))

    if current.expires_at < datetime.now(UTC):
        raise RefreshTokenInvalidError("expired refresh token")

    user = await session.get(User, current.user_id)
    if user is None:
        raise RefreshTokenInvalidError("user no longer exists")

    tokens, new_row = await _issue_token_pair(session, user)
    current.revoked_at = datetime.now(UTC)
    current.replaced_by_id = new_row.id
    await session.commit()

    return user, tokens


async def revoke_refresh_token(session: AsyncSession, raw_refresh_token: str) -> None:
    token_hash = hash_refresh_token(raw_refresh_token)
    current = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    if current is not None and current.revoked_at is None:
        current.revoked_at = datetime.now(UTC)
        await session.commit()


async def _revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    result = await session.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
    )
    now = datetime.now(UTC)
    for token in result:
        token.revoked_at = now
    await session.commit()
