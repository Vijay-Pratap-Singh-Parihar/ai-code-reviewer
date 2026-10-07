"""Create this deployment's GitHub App from inside revu (manifest flow).

1. `start_manifest` builds the manifest (permissions, events, callback and
   webhook URLs; a smee.io channel for the webhook when there's no public
   URL) and a signed `state` naming the user who started it.
2. The browser POSTs the manifest to GitHub, the user confirms there, and
   GitHub redirects to the web app's `/github/app-created?code=…&state=…`.
3. `complete_manifest` checks `state` (signature, expiry, same user),
   exchanges the one-time code for the App's credentials and stores them
   encrypted. There is only ever one App, so a second completion is refused.

The `state` is what stops a forged redirect: without the server's signing
key nobody can mint one, and it only completes for the user it names.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from db.audit import AuditAction, record_audit
from db.github import GitHubAppCredentials
from db.organization import User
from ghapp import convert_manifest_code, create_smee_channel
from ghapp.manifest import build_manifest, creation_url
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.github import resolve_app, secret_box

_STATE_PURPOSE = "github-app-manifest"
# GitHub's manifest code itself is valid for one hour.
_STATE_LIFETIME = timedelta(hours=1)


class AppAlreadyConfiguredError(Exception):
    pass


class InvalidManifestStateError(Exception):
    pass


@dataclass(frozen=True)
class ManifestStart:
    action_url: str
    manifest: dict[str, Any]


def _sign_state(user: User, webhook_proxy_url: str | None) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "purpose": _STATE_PURPOSE,
        "sub": str(user.id),
        "proxy": webhook_proxy_url,
        "nonce": secrets.token_urlsafe(16),
        "iat": int(now.timestamp()),
        "exp": int((now + _STATE_LIFETIME).timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _verify_state(state: str, user: User) -> dict[str, Any]:
    settings = get_settings()
    try:
        claims: dict[str, Any] = jwt.decode(
            state, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:
        raise InvalidManifestStateError(f"invalid or expired state: {exc}") from exc
    if claims.get("purpose") != _STATE_PURPOSE or claims.get("sub") != str(user.id):
        raise InvalidManifestStateError("state was issued for a different user or purpose")
    return claims


async def start_manifest(
    session: AsyncSession, *, user: User, organization: str | None
) -> ManifestStart:
    if await resolve_app(session) is not None:
        raise AppAlreadyConfiguredError()

    settings = get_settings()
    if settings.github_webhook_public_url:
        webhook_url = settings.github_webhook_public_url
        proxy_url: str | None = None
    else:
        proxy_url = await create_smee_channel(smee_url=settings.smee_url)
        webhook_url = proxy_url

    state = _sign_state(user, proxy_url)
    return ManifestStart(
        action_url=creation_url(
            web_url=settings.github_web_url, state=state, organization=organization
        ),
        manifest=build_manifest(web_app_url=settings.web_app_url, webhook_url=webhook_url),
    )


async def complete_manifest(
    session: AsyncSession, *, user: User, code: str, state: str
) -> GitHubAppCredentials:
    claims = _verify_state(state, user)
    if await resolve_app(session) is not None:
        raise AppAlreadyConfiguredError()

    settings = get_settings()
    created = await convert_manifest_code(code, api_url=settings.github_api_url)
    box = secret_box()
    row = GitHubAppCredentials(
        id=1,
        app_id=int(created["id"]),
        slug=str(created["slug"]),
        name=str(created.get("name") or created["slug"]),
        owner_login=str((created.get("owner") or {}).get("login", "unknown")),
        html_url=str(
            created.get("html_url") or f"{settings.github_web_url}/apps/{created['slug']}"
        ),
        client_id=str(created["client_id"]),
        client_secret_enc=box.encrypt(str(created["client_secret"])),
        webhook_secret_enc=box.encrypt(str(created["webhook_secret"])),
        private_key_enc=box.encrypt(str(created["pem"])),
        webhook_proxy_url=claims.get("proxy"),
        created_by_user_id=uuid.UUID(str(user.id)),
    )
    session.add(row)
    record_audit(
        session,
        org_id=user.org_id,
        actor_id=user.id,
        action=AuditAction.GITHUB_APP_CREATED,
        target=f"github_app:{row.slug}",
        metadata={"app_id": row.app_id, "owner": row.owner_login},
    )
    await session.commit()
    return row
