import logging
from pathlib import Path
from typing import Any, Literal

from arq.connections import RedisSettings
from arq.cron import cron
from arq.worker import func
from db.tenancy import rls_bypass_reason
from ghapp import GitHubAppConfig, load_private_key
from observability import configure_logging, default_format
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from worker.github import make_client_factory
from worker.job_context import traced
from worker.jobs.analyze import REVIEW_JOB_TIMEOUT_S, REVIEW_MAX_TRIES, analyze_pr, fail_stuck_runs
from worker.jobs.db_ping import db_ping
from worker.jobs.github import review_github_pr, sync_and_index_branch
from worker.jobs.ping import ping
from worker.jobs.post_review import post_github_review
from worker.jobs.retention import (
    purge_expired_data,
    purge_installation_data,
    purge_repository_data,
)
from worker.providers import ProviderAccess
from worker.storage import TenantStorage, signing_key_from_secret

# apps/worker/src/worker/settings.py -> repo root is four levels up.
_REPO_ROOT_ENV = Path(__file__).resolve().parents[4] / ".env"

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", _REPO_ROOT_ENV), extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"
    log_format: Literal["json", "text"] | None = None
    # Same meaning as the API's: may AI provider endpoints be on private or
    # loopback networks? Unset = yes outside production, no in production.
    revu_allow_private_provider_urls: bool | None = None
    redis_url: str = "redis://localhost:6379/0"
    # Row-level security applies: see `db.tenancy`. Jobs bind each session
    # to the organisation named in their arguments.
    database_url: str = (
        "postgresql+asyncpg://revu_app:revu_app_dev_password@localhost:5432/revu"
    )

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
    # Signed branch-index blobs (graph + full index result).
    revu_index_storage_dir: str = ".revu/index-graphs"
    # How long a disconnected repository's data is kept before
    # `purge_expired_data` deletes it. Must match the API's setting.
    revu_disconnected_retention_days: int = 30

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


def _configure_logging() -> None:
    configure_logging(
        service="worker",
        level=settings.log_level,
        fmt=settings.log_format or default_format(settings.environment),
    )


_configure_logging()

# A branch index of a large repository clones and parses it all.
INDEX_JOB_TIMEOUT_S = 30 * 60


class WorkerSettings:
    """ARQ worker entrypoint config."""

    # arq's default 5-minute limit would cut off a review on a local model
    # (several minutes per part on a laptop CPU); reviews and index builds
    # get their own. Reviews retry transient provider errors themselves
    # (`worker.jobs.analyze.execute_review`), up to REVIEW_MAX_TRIES.
    functions = [
        func(traced(ping)),
        func(traced(db_ping)),
        func(traced(analyze_pr), timeout=REVIEW_JOB_TIMEOUT_S, max_tries=REVIEW_MAX_TRIES),
        func(traced(review_github_pr), timeout=REVIEW_JOB_TIMEOUT_S, max_tries=REVIEW_MAX_TRIES),
        func(traced(sync_and_index_branch), timeout=INDEX_JOB_TIMEOUT_S),
        func(traced(purge_repository_data)),
        func(traced(purge_installation_data)),
        func(traced(post_github_review)),
    ]
    job_timeout = 10 * 60
    cron_jobs = [
        # Hourly, off the hour so it doesn't coincide with other cron work.
        cron(traced(purge_expired_data), minute={17}, unique=True),
        # Runs a crashed worker left "running" or "queued" for good.
        cron(traced(fail_stuck_runs), minute=set(range(5, 60, 10)), unique=True),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        # The `arq` command applies its own logging config after importing
        # this module; put ours back so each line is written once, in format.
        _configure_logging()
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        problem = await rls_bypass_reason(engine)
        if problem and settings.environment == "production":
            raise RuntimeError(f"refusing to start: {problem}")
        if problem:
            logger.warning("TENANT ISOLATION NOT ENFORCED BY THE DATABASE: %s", problem)
        ctx["db_engine"] = engine
        ctx["db_session_factory"] = async_sessionmaker(engine, expire_on_commit=False)

        config = settings.github_app_config()
        ctx["github_client_factory"] = make_client_factory(config) if config else None
        if config is None:
            logger.info("No GitHub App in env; jobs will use one stored in the database, if any")
        ctx["credential_encryption_key"] = settings.credential_encryption_key
        ctx["github_api_url"] = settings.github_api_url
        ctx["git_base_url"] = settings.github_web_url
        ctx["retention_days"] = settings.revu_disconnected_retention_days
        allow_private = settings.revu_allow_private_provider_urls
        ctx["provider_access"] = ProviderAccess(
            secret=settings.credential_encryption_key,
            allow_private=(
                settings.environment != "production" if allow_private is None else allow_private
            ),
        )
        ctx["storage"] = TenantStorage(
            repo_cache_root=Path(settings.revu_repo_cache_dir).resolve(),
            index_root=Path(settings.revu_index_storage_dir).resolve(),
            signing_key=signing_key_from_secret(settings.credential_encryption_key),
        )

    @staticmethod
    async def on_shutdown(ctx: dict[str, Any]) -> None:
        await ctx["db_engine"].dispose()
