"""Worker-side GitHub access: opening a client for this deployment's App and
resolving a repository row to an installation token.

The App comes from `GITHUB_*` env vars when set (a factory registered on the
ARQ ctx at startup), else from the credentials the in-app manifest flow
stored encrypted in `github_app_credentials`, read per job so an App
created after the worker started is picked up without a restart."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from db.github import GitHubAppCredentials
from db.organization import GithubInstallation
from db.repository import Repository
from ghapp import GitHubAppConfig, GitHubAppNotConfiguredError, GitHubClient, SecretBox
from sqlalchemy.ext.asyncio import AsyncSession

GitHubClientFactory = Callable[[], GitHubClient]


class RepositoryNotConnectedError(RuntimeError):
    pass


async def open_client(ctx: dict[str, Any], session: AsyncSession) -> GitHubClient:
    factory: GitHubClientFactory | None = ctx.get("github_client_factory")
    if factory is not None:
        return factory()
    row = await session.get(GitHubAppCredentials, 1)
    if row is None:
        raise GitHubAppNotConfiguredError("GitHub App credentials are not configured")
    box = SecretBox(ctx.get("credential_encryption_key") or "")
    return GitHubClient(
        GitHubAppConfig(
            app_id=str(row.app_id),
            private_key_pem=box.decrypt(row.private_key_enc),
            client_id=row.client_id,
            api_url=ctx.get("github_api_url", "https://api.github.com"),
            web_url=ctx.get("git_base_url", "https://github.com"),
        )
    )


def make_client_factory(config: GitHubAppConfig) -> GitHubClientFactory:
    return lambda: GitHubClient(config)


async def installation_token(session: AsyncSession, gh: GitHubClient, repo: Repository) -> str:
    installation = (
        await session.get(GithubInstallation, repo.installation_id)
        if repo.installation_id
        else None
    )
    if installation is None or not repo.is_active:
        raise RepositoryNotConnectedError(
            f"{repo.full_name} is not connected through the GitHub App"
        )
    return await gh.create_installation_token(installation.installation_id)


def remote_url(ctx: dict[str, Any], full_name: str) -> str:
    return f"{ctx['git_base_url'].rstrip('/')}/{full_name}.git"
