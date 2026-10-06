from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from ghapp import GitHubAppConfig, GitHubAppNotConfiguredError, GitHubClient, load_private_key

from api.core.config import get_settings


def github_app_config() -> GitHubAppConfig:
    settings = get_settings()
    if not settings.github_app_configured:
        raise GitHubAppNotConfiguredError("GitHub App credentials are not configured")
    return GitHubAppConfig(
        app_id=settings.github_app_id,
        private_key_pem=load_private_key(
            key_text=settings.github_app_private_key,
            key_path=settings.github_app_private_key_path,
        ),
        client_id=settings.github_client_id,
        client_secret=settings.github_client_secret,
        api_url=settings.github_api_url,
        web_url=settings.github_web_url,
    )


async def get_github_client() -> AsyncIterator[GitHubClient]:
    """A per-request GitHub client. A FastAPI dependency so tests can swap
    in one backed by `httpx.MockTransport` via `app.dependency_overrides`.
    """
    try:
        config = github_app_config()
    except (GitHubAppNotConfiguredError, OSError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"GitHub App is not configured: {exc}"
        ) from exc
    async with GitHubClient(config) as client:
        yield client


GitHub = Annotated[GitHubClient, Depends(get_github_client)]
