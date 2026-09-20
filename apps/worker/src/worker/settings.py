from pathlib import Path
from typing import Any

from arq.connections import RedisSettings
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from worker.jobs.db_ping import db_ping
from worker.jobs.ping import ping

# apps/worker/src/worker/settings.py -> repo root is four levels up.
_REPO_ROOT_ENV = Path(__file__).resolve().parents[4] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", _REPO_ROOT_ENV), extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql+asyncpg://revu:revu_dev_password@localhost:5432/revu"


settings = Settings()


class WorkerSettings:
    """ARQ worker entrypoint config. Real analysis jobs are registered here in Stage 3."""

    functions = [ping, db_ping]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        ctx["db_engine"] = engine
        ctx["db_session_factory"] = async_sessionmaker(engine, expire_on_commit=False)

    @staticmethod
    async def on_shutdown(ctx: dict[str, Any]) -> None:
        await ctx["db_engine"].dispose()
