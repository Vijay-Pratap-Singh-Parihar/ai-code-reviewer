from typing import Annotated

from arq import ArqRedis
from db.organization import User, UserRole
from db.tenancy import bind_org
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.queue import get_redis_pool
from api.core.security import InvalidTokenError, decode_access_token
from api.db.session import get_db

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")

    try:
        claims = decode_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    user = await db.get(User, claims.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user no longer exists")

    # Everything this request does from here on runs under row-level
    # security for the user's organisation (the route gets the same session).
    await bind_org(db, user.org_id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_org_admin(user: CurrentUser) -> User:
    """Owners and admins of the organisation: settings, data deletion, audit."""
    if user.role not in (UserRole.OWNER, UserRole.ADMIN):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "only an organization owner or admin can do this"
        )
    return user


async def require_platform_admin(user: CurrentUser) -> User:
    """The deployment's operator, not any organisation's owner: every
    organisation installs the same GitHub App, so creating it is not an
    organisation-level decision."""
    if not user.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only a platform admin can do this")
    return user


OrgAdmin = Annotated[User, Depends(require_org_admin)]
PlatformAdmin = Annotated[User, Depends(require_platform_admin)]
RedisPool = Annotated[ArqRedis, Depends(get_redis_pool)]
