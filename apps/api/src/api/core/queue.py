import asyncio

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from observability import request_id

from api.core.config import get_settings

settings = get_settings()

_pool: ArqRedis | None = None
_pool_lock = asyncio.Lock()


async def get_redis_pool() -> ArqRedis:
    """A process-wide ARQ connection pool, created on first use.

    A FastAPI dependency (not a bare module import) specifically so tests can
    override it via `app.dependency_overrides` to point at a test Redis
    instance, the same way `get_db` is overridden for the test database.
    """
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def enqueue(redis: ArqRedis, function: str, *args: object) -> None:
    """Queue a worker job carrying the current request's id, so the job's log
    lines can be found together with the request that started it."""
    await redis.enqueue_job(function, *args, request_id=request_id())
