from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/src/api/core/config.py -> repo root is five levels up.
_REPO_ROOT_ENV = Path(__file__).resolve().parents[5] / ".env"


class Settings(BaseSettings):
    """Application configuration, sourced from environment variables / .env.

    Inside Docker, env vars are injected directly by docker-compose and this
    class reads them the normal way. Outside Docker (e.g. running Alembic
    locally from `apps/api`), `.env` lives at the repo root regardless of
    cwd, so it's loaded from there explicitly.
    """

    model_config = SettingsConfigDict(env_file=(".env", _REPO_ROOT_ENV), extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"
    api_cors_origins: str = "http://localhost:3000"

    database_url: str = "postgresql+asyncpg://revu:revu_dev_password@localhost:5432/revu"
    database_url_sync: str = "postgresql+psycopg://revu:revu_dev_password@localhost:5432/revu"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: str = "change-me-dev-only-not-for-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 30

    # Model used by the Stage 3 diff-only reviewer. Snapshotted onto each
    # AnalysisRun.config_snapshot at enqueue time, so a run always records
    # exactly which model produced it regardless of later config changes.
    revu_model_review: str = "claude-sonnet-5"

    # GitHub App (Stage 10). All optional: with them unset the API still runs
    # and the manual diff-paste flow still works; `/github/*` reports the App
    # as not configured. The private key comes from a file path (preferred) or
    # inline PEM text with literal "\n" escapes.
    github_app_id: str = ""
    github_app_slug: str = ""
    github_app_private_key: str = ""
    github_app_private_key_path: str = ""
    github_webhook_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    github_api_url: str = "https://api.github.com"
    github_web_url: str = "https://github.com"

    # One-click App creation (manifest flow). The App's credentials are then
    # stored encrypted in the database instead of the variables above.
    credential_encryption_key: str = "change-me-dev-only-not-for-production"
    # Where the browser reaches the web app; GitHub redirects back here.
    web_app_url: str = "http://localhost:3000"
    # A publicly reachable URL for POST /github/webhook. When empty (local
    # development), a smee.io channel relays webhooks instead.
    github_webhook_public_url: str = ""
    smee_url: str = "https://smee.io"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def github_app_configured(self) -> bool:
        return bool(
            self.github_app_id
            and self.github_app_slug
            and (self.github_app_private_key or self.github_app_private_key_path)
            and self.github_client_id
            and self.github_client_secret
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
