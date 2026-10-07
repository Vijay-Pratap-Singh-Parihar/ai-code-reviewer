"""Which GitHub App this deployment uses, and a client for it.

Two sources, in order: the `GITHUB_*` environment variables (when complete),
else the credentials row the in-app manifest flow stored, encrypted, in
`github_app_credentials`. Env wins so an operator can always pin the App
explicitly.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Literal

from db.github import GitHubAppCredentials
from fastapi import Depends, HTTPException, status
from ghapp import (
    CredentialDecryptionError,
    GitHubAppConfig,
    GitHubAppNotConfiguredError,
    GitHubClient,
    SecretBox,
    is_placeholder_secret,
    load_private_key,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.db.session import get_db


@dataclass(frozen=True)
class ResolvedApp:
    source: Literal["env", "database"]
    config: GitHubAppConfig
    slug: str
    webhook_secret: str
    html_url: str | None = None
    webhook_proxy_url: str | None = None


def secret_box() -> SecretBox:
    settings = get_settings()
    if settings.is_production and is_placeholder_secret(settings.credential_encryption_key):
        raise GitHubAppNotConfiguredError(
            "CREDENTIAL_ENCRYPTION_KEY is unset or the placeholder; refusing to store or "
            "read credentials with it in production"
        )
    return SecretBox(settings.credential_encryption_key)


async def resolve_app(session: AsyncSession) -> ResolvedApp | None:
    settings = get_settings()
    if settings.github_app_configured:
        return ResolvedApp(
            source="env",
            config=GitHubAppConfig(
                app_id=settings.github_app_id,
                private_key_pem=load_private_key(
                    key_text=settings.github_app_private_key,
                    key_path=settings.github_app_private_key_path,
                ),
                client_id=settings.github_client_id,
                client_secret=settings.github_client_secret,
                api_url=settings.github_api_url,
                web_url=settings.github_web_url,
            ),
            slug=settings.github_app_slug,
            webhook_secret=settings.github_webhook_secret,
        )

    row = await session.get(GitHubAppCredentials, 1)
    if row is None:
        return None
    box = secret_box()
    return ResolvedApp(
        source="database",
        config=GitHubAppConfig(
            app_id=str(row.app_id),
            private_key_pem=box.decrypt(row.private_key_enc),
            client_id=row.client_id,
            client_secret=box.decrypt(row.client_secret_enc),
            api_url=settings.github_api_url,
            web_url=settings.github_web_url,
        ),
        slug=row.slug,
        webhook_secret=box.decrypt(row.webhook_secret_enc),
        html_url=row.html_url,
        webhook_proxy_url=row.webhook_proxy_url,
    )


async def get_github_client(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AsyncIterator[GitHubClient]:
    """A per-request GitHub client. A FastAPI dependency so tests can swap
    in one backed by `httpx.MockTransport` via `app.dependency_overrides`.
    """
    try:
        app = await resolve_app(db)
    except (GitHubAppNotConfiguredError, CredentialDecryptionError, OSError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"GitHub App is not usable: {exc}"
        ) from exc
    if app is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "GitHub App is not configured: create one from the GitHub page first",
        )
    async with GitHubClient(app.config) as client:
        yield client


GitHub = Annotated[GitHubClient, Depends(get_github_client)]
