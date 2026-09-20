from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.deps import CurrentUser
from api.db.session import get_db
from api.models.organization import User
from api.schemas.auth import AccessTokenResponse, LoginRequest, SignupRequest, UserPublic
from api.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/auth"


def _set_refresh_cookie(response: Response, tokens: auth_service.TokenPair) -> None:
    max_age = settings.jwt_refresh_token_expire_days * 24 * 60 * 60
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=tokens.refresh_token,
        max_age=max_age,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
    )


def _token_response(user: User, tokens: auth_service.TokenPair) -> AccessTokenResponse:
    return AccessTokenResponse(
        access_token=tokens.access_token,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        user=UserPublic.model_validate(user),
    )


@router.post("/signup", response_model=AccessTokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest, response: Response, db: Annotated[AsyncSession, Depends(get_db)]
) -> AccessTokenResponse:
    try:
        user, tokens = await auth_service.signup(
            db, org_name=body.org_name, email=body.email, password=body.password
        )
    except auth_service.EmailAlreadyRegisteredError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered") from exc

    _set_refresh_cookie(response, tokens)
    return _token_response(user, tokens)


@router.post("/login", response_model=AccessTokenResponse)
async def login(
    body: LoginRequest, response: Response, db: Annotated[AsyncSession, Depends(get_db)]
) -> AccessTokenResponse:
    try:
        user, tokens = await auth_service.authenticate(db, email=body.email, password=body.password)
    except auth_service.InvalidCredentialsError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password") from exc

    _set_refresh_cookie(response, tokens)
    return _token_response(user, tokens)


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> AccessTokenResponse:
    if refresh_token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing refresh token")

    try:
        user, tokens = await auth_service.rotate_refresh_token(db, refresh_token)
    except auth_service.RefreshTokenReusedError as exc:
        response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "refresh token reuse detected; all sessions revoked"
        ) from exc
    except auth_service.RefreshTokenInvalidError as exc:
        response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token") from exc

    _set_refresh_cookie(response, tokens)
    return _token_response(user, tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> None:
    if refresh_token is not None:
        await auth_service.revoke_refresh_token(db, refresh_token)
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)


@router.get("/me", response_model=UserPublic)
async def me(user: CurrentUser) -> UserPublic:
    return UserPublic.model_validate(user)
