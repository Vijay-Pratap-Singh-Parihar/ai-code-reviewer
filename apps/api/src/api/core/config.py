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

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
