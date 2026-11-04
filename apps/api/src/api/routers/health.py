"""Health checks for an orchestrator (Docker, Kubernetes, a load balancer).

- `/health/live`: the process is up and serving. Restart it if this fails.
- `/health/ready`: it can do its job: Postgres and Redis answer. Take it out
  of rotation while this returns 503, but don't restart it.

Readiness also says whether a worker has checked in recently. That is
reported, not required: the API serves reads without a worker, and the
worker being down is an alert for the operator, not a reason to stop
sending the API traffic.
"""

import asyncio
import logging
from typing import Annotated, Any

from arq import ArqRedis
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text

from api.core.queue import get_redis_pool
from api.db.session import engine

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

CHECK_TIMEOUT_S = 2.0
# arq refreshes this key every `health_check_interval` (60 s by default) and
# lets it expire shortly after; present = a worker is consuming the queue.
WORKER_HEALTH_KEY = "arq:queue:health-check"


@router.get("/health")
@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


async def _database() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check(name: str, probe: Any) -> str:
    try:
        await asyncio.wait_for(probe, CHECK_TIMEOUT_S)
        return "ok"
    except Exception as exc:  # any failure means "not ready", whatever raised it
        logger.warning("readiness check %s failed: %s", name, exc)
        return "unavailable"


@router.get("/health/ready")
async def ready(
    response: Response, redis: Annotated[ArqRedis, Depends(get_redis_pool)]
) -> dict[str, Any]:
    checks = {
        "database": await _check("database", _database()),
        "redis": await _check("redis", redis.ping()),
    }
    ok = all(state == "ok" for state in checks.values())
    worker = "unknown"
    if checks["redis"] == "ok":
        try:
            worker = "ok" if await redis.exists(WORKER_HEALTH_KEY) else "not seen"
        except Exception:
            worker = "unknown"
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "unavailable", "checks": checks, "worker": worker}
