"""Worker-side GitHub access: the client factory registered on the ARQ ctx
and a helper that resolves a repository row to an installation token."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from db.organization import GithubInstallation
from db.repository import Repository
from ghapp import GitHubAppConfig, GitHubAppNotConfiguredError, GitHubClient
from sqlalchemy.ext.asyncio import AsyncSession

GitHubClientFactory = Callable[[], GitHubClient]


class RepositoryNotConnectedError(RuntimeError):
    pass


def client_factory(ctx: dict[str, Any]) -> GitHubClientFactory:
    factory: GitHubClientFactory | None = ctx.get("github_client_factory")
    if factory is None:
        raise GitHubAppNotConfiguredError("GitHub App credentials are not configured")
    return factory


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
