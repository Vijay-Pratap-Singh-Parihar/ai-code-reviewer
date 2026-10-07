import json
import logging
import uuid
from typing import Annotated, Any

from db.audit import AuditAction, record_audit
from db.organization import GithubInstallation
from db.repository import Repository
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from ghapp import (
    CredentialDecryptionError,
    GitHubAppNotConfiguredError,
    GitHubError,
    verify_signature,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.deps import CurrentUser, OrgAdmin, PlatformAdmin, RedisPool
from api.core.github import GitHub, ResolvedApp, resolve_app
from api.db.session import get_db
from api.schemas.github import (
    GitHubAppInfo,
    InstallationLinkRequest,
    InstallationPublic,
    ManifestCompleteRequest,
    ManifestStartRequest,
    ManifestStartResponse,
    WebhookResponse,
)
from api.services import github as github_service
from api.services import github_app as github_app_service
from api.services import lifecycle

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["github"])

Db = Annotated[AsyncSession, Depends(get_db)]


async def _to_public(db: AsyncSession, installation: GithubInstallation) -> InstallationPublic:
    count = await db.scalar(
        select(func.count())
        .select_from(Repository)
        .where(Repository.installation_id == installation.id, Repository.is_active.is_(True))
    )
    disconnected_count, earliest_disconnect = (
        await db.execute(
            select(func.count(), func.min(Repository.disconnected_at)).where(
                Repository.installation_id == installation.id,
                Repository.disconnected_at.is_not(None),
            )
        )
    ).one()
    return InstallationPublic(
        id=installation.id,
        installation_id=installation.installation_id,
        account_login=installation.account_login,
        account_type=installation.account_type,
        installed_at=installation.installed_at,
        repository_count=int(count or 0),
        status=lifecycle.installation_status(installation),  # type: ignore[arg-type]
        suspended_at=installation.suspended_at,
        uninstalled_at=installation.uninstalled_at,
        disconnected_repository_count=int(disconnected_count or 0),
        purge_after=lifecycle.purge_after(earliest_disconnect),
    )


def _bad_gateway(exc: GitHubError) -> HTTPException:
    return HTTPException(status.HTTP_502_BAD_GATEWAY, f"GitHub request failed: {exc}")


def _app_info(app: ResolvedApp | None, *, error: str | None = None) -> GitHubAppInfo:
    if app is None:
        return GitHubAppInfo(
            configured=False, install_url=None, webhook_configured=False, error=error
        )
    web = get_settings().github_web_url
    return GitHubAppInfo(
        configured=True,
        install_url=f"{web}/apps/{app.slug}/installations/new",
        webhook_configured=bool(app.webhook_secret),
        source=app.source,
        slug=app.slug,
        app_url=app.html_url or f"{web}/apps/{app.slug}",
        webhook_proxy_url=app.webhook_proxy_url,
    )


@router.get("/app", response_model=GitHubAppInfo)
async def get_app_info(_: CurrentUser, db: Db) -> GitHubAppInfo:
    try:
        return _app_info(await resolve_app(db))
    except (GitHubAppNotConfiguredError, CredentialDecryptionError, OSError) as exc:
        return _app_info(None, error=str(exc))


@router.post("/app/manifest", response_model=ManifestStartResponse)
async def start_app_manifest(
    body: ManifestStartRequest, user: PlatformAdmin, db: Db
) -> ManifestStartResponse:
    """Step 1 of one-click App creation: what the browser should POST to
    GitHub. Platform admins only: every organisation installs this App."""
    try:
        start = await github_app_service.start_manifest(
            db, user=user, organization=body.organization or None
        )
    except github_app_service.AppAlreadyConfiguredError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "a GitHub App is already set up") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except GitHubError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"could not create a webhook relay channel: {exc}"
        ) from exc
    return ManifestStartResponse(action_url=start.action_url, manifest=start.manifest)


@router.post("/app/conversions", response_model=GitHubAppInfo, status_code=status.HTTP_201_CREATED)
async def complete_app_manifest(
    body: ManifestCompleteRequest, user: PlatformAdmin, db: Db
) -> GitHubAppInfo:
    """Step 2: GitHub redirected back with a one-time code; trade it for the
    new App's credentials and store them encrypted."""
    try:
        await github_app_service.complete_manifest(db, user=user, code=body.code, state=body.state)
    except github_app_service.InvalidManifestStateError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except (github_app_service.AppAlreadyConfiguredError, IntegrityError) as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "a GitHub App is already set up") from exc
    except GitHubAppNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except GitHubError as exc:
        raise _bad_gateway(exc) from exc
    return _app_info(await resolve_app(db))


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
            db,
            gh,
            org_id=user.org_id,
            installation_id=body.installation_id,
            code=body.code,
            actor_id=user.id,
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
    if installation.uninstalled_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "the GitHub App was uninstalled from this account"
        )
    try:
        await github_service.resync_installation(db, gh, installation)
    except GitHubError as exc:
        raise _bad_gateway(exc) from exc
    record_audit(
        db,
        org_id=installation.org_id,
        actor_id=user.id,
        action=AuditAction.INSTALLATION_SYNCED,
        target=f"installation:{installation.account_login}",
    )
    await db.commit()
    return await _to_public(db, installation)


@router.delete("/installations/{installation_id}/data", status_code=status.HTTP_202_ACCEPTED)
async def delete_installation_data(
    installation_id: uuid.UUID, user: OrgAdmin, db: Db, redis: RedisPool
) -> dict[str, str]:
    """Delete everything kept for an uninstalled connection now, instead of
    waiting for the retention period: its repositories with their reviews,
    findings and indexes, their clones and index files, and the connection
    itself. The audit log keeps the record that it happened."""
    installation = await db.scalar(
        select(GithubInstallation).where(
            GithubInstallation.id == installation_id, GithubInstallation.org_id == user.org_id
        )
    )
    if installation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "installation not found")
    try:
        await lifecycle.request_installation_purge(db, redis, installation=installation, actor=user)
    except lifecycle.StillConnectedError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "uninstall the GitHub App from this account first; otherwise GitHub would sync "
            "the repositories straight back",
        ) from exc
    return {"status": "queued"}


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
    # An explicit env secret wins; otherwise the one GitHub generated for an
    # App created through the manifest flow.
    secret = get_settings().github_webhook_secret
    if not secret:
        try:
            app = await resolve_app(db)
        except (GitHubAppNotConfiguredError, CredentialDecryptionError, OSError):
            app = None
        secret = app.webhook_secret if app else ""
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
