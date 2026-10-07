import logging
from pathlib import Path
from typing import Any

from arq.connections import RedisSettings
from ghapp import GitHubAppConfig, load_private_key
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from worker.github import make_client_factory
from worker.jobs.analyze import analyze_pr
from worker.jobs.db_ping import db_ping
from worker.jobs.github import review_github_pr, sync_and_index_branch
from worker.jobs.index_branch import update_branch_index
from worker.jobs.ping import ping

# apps/worker/src/worker/settings.py -> repo root is four levels up.
_REPO_ROOT_ENV = Path(__file__).resolve().parents[4] / ".env"

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", _REPO_ROOT_ENV), extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql+asyncpg://revu:revu_dev_password@localhost:5432/revu"

    # GitHub App credentials (same variables the API reads). Only the App ID
    # and private key matter here: the worker mints installation tokens.
    github_app_id: str = ""
    github_app_private_key: str = ""
    github_app_private_key_path: str = ""
    github_api_url: str = "https://api.github.com"
    github_web_url: str = "https://github.com"
    # Decrypts App credentials stored by the in-app manifest flow (must
    # match the API's value).
    credential_encryption_key: str = "change-me-dev-only-not-for-production"
    # Local git cache for GitHub-connected repos (index builds, cross-file).
    revu_repo_cache_dir: str = ".revu/repos"

    def github_app_config(self) -> GitHubAppConfig | None:
        if not self.github_app_id or not (
            self.github_app_private_key or self.github_app_private_key_path
        ):
            return None
        return GitHubAppConfig(
            app_id=self.github_app_id,
            private_key_pem=load_private_key(
                key_text=self.github_app_private_key, key_path=self.github_app_private_key_path
            ),
            api_url=self.github_api_url,
            web_url=self.github_web_url,
        )


settings = Settings()


class WorkerSettings:
    """ARQ worker entrypoint config."""

    functions = [
        ping,
        db_ping,
        analyze_pr,
        update_branch_index,
        review_github_pr,
        sync_and_index_branch,
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        ctx["db_engine"] = engine
        ctx["db_session_factory"] = async_sessionmaker(engine, expire_on_commit=False)

        config = settings.github_app_config()
        ctx["github_client_factory"] = make_client_factory(config) if config else None
        if config is None:
            logger.info("No GitHub App in env; jobs will use one stored in the database, if any")
        ctx["credential_encryption_key"] = settings.credential_encryption_key
        ctx["github_api_url"] = settings.github_api_url
        ctx["git_base_url"] = settings.github_web_url
        ctx["repo_cache_dir"] = Path(settings.revu_repo_cache_dir).resolve()

    @staticmethod
    async def on_shutdown(ctx: dict[str, Any]) -> None:
        await ctx["db_engine"].dispose()
