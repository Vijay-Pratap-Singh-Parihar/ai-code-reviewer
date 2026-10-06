import json
import logging
import uuid
from typing import Annotated, Any

from db.organization import GithubInstallation
from db.repository import Repository
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from ghapp import GitHubError, verify_signature
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.deps import CurrentUser, RedisPool
from api.core.github import GitHub
from api.db.session import get_db
from api.schemas.github import (
    GitHubAppInfo,
    InstallationLinkRequest,
    InstallationPublic,
    WebhookResponse,
)
from api.services import github as github_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["github"])

Db = Annotated[AsyncSession, Depends(get_db)]


async def _to_public(db: AsyncSession, installation: GithubInstallation) -> InstallationPublic:
    count = await db.scalar(
        select(func.count())
        .select_from(Repository)
        .where(Repository.installation_id == installation.id, Repository.is_active.is_(True))
    )
    return InstallationPublic(
        id=installation.id,
        installation_id=installation.installation_id,
        account_login=installation.account_login,
        account_type=installation.account_type,
        installed_at=installation.installed_at,
        repository_count=int(count or 0),
    )


def _bad_gateway(exc: GitHubError) -> HTTPException:
    return HTTPException(status.HTTP_502_BAD_GATEWAY, f"GitHub request failed: {exc}")


@router.get("/app", response_model=GitHubAppInfo)
async def get_app_info(_: CurrentUser) -> GitHubAppInfo:
    settings = get_settings()
    install_url = (
        f"{settings.github_web_url}/apps/{settings.github_app_slug}/installations/new"
        if settings.github_app_configured
        else None
    )
    return GitHubAppInfo(
        configured=settings.github_app_configured,
        install_url=install_url,
        webhook_configured=bool(settings.github_webhook_secret),
    )


@router.get("/installations", response_model=list[InstallationPublic])
async def list_installations(user: CurrentUser, db: Db) -> list[InstallationPublic]:
    rows = await db.scalars(
        select(GithubInstallation)
        .where(GithubInstallation.org_id == user.org_id)
        .order_by(GithubInstallation.installed_at)
    )
    return [await _to_public(db, row) for row in rows]


@router.post(
    "/installations", response_model=InstallationPublic, status_code=status.HTTP_201_CREATED
)
async def link_installation(
    body: InstallationLinkRequest, user: CurrentUser, db: Db, gh: GitHub
) -> InstallationPublic:
    try:
        installation = await github_service.link_installation(
            db, gh, org_id=user.org_id, installation_id=body.installation_id, code=body.code
        )
    except github_service.InstallationNotAccessibleError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "your GitHub account cannot access this installation"
        ) from exc
    except github_service.InstallationOwnedByAnotherOrgError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "this installation is linked to a different organization"
        ) from exc
    except GitHubError as exc:
        raise _bad_gateway(exc) from exc
    return await _to_public(db, installation)


@router.post("/installations/{installation_id}/sync", response_model=InstallationPublic)
async def sync_installation(
    installation_id: uuid.UUID, user: CurrentUser, db: Db, gh: GitHub
) -> InstallationPublic:
    installation = await db.scalar(
        select(GithubInstallation).where(
            GithubInstallation.id == installation_id, GithubInstallation.org_id == user.org_id
        )
    )
    if installation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "installation not found")
    try:
        await github_service.resync_installation(db, gh, installation)
    except GitHubError as exc:
        raise _bad_gateway(exc) from exc
    await db.commit()
    return await _to_public(db, installation)


@router.post("/webhook", response_model=WebhookResponse)
async def receive_webhook(
    request: Request,
    db: Db,
    redis: RedisPool,
    x_github_event: Annotated[str, Header()],
    x_github_delivery: Annotated[str, Header(max_length=64)],
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> WebhookResponse:
    """Unauthenticated by design (GitHub calls it), so the HMAC signature is
    the only gate, and it is checked against the raw bytes before parsing."""
    secret = get_settings().github_webhook_secret
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "webhook secret not configured")

    body = await request.body()
    if not verify_signature(secret, body, x_hub_signature_256):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid webhook signature")

    try:
        payload: Any = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "body must be a JSON object")

    try:
        outcome = await github_service.handle_webhook(
            db, redis, delivery_id=x_github_delivery, event=x_github_event, payload=payload
        )
    except (KeyError, TypeError, ValueError) as exc:
        # Rolling back also un-records the delivery, so a redelivery after a
        # fix is processed rather than skipped as a duplicate.
        await db.rollback()
        logger.warning("webhook %s (%s) malformed: %r", x_github_delivery, x_github_event, exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unexpected payload shape") from exc

    logger.info(
        "webhook %s %s/%s -> %s (%s)",
        x_github_delivery,
        x_github_event,
        payload.get("action"),
        outcome.status,
        outcome.detail,
    )
    return WebhookResponse(
        status=outcome.status, detail=outcome.detail, enqueued=outcome.enqueued or []
    )
