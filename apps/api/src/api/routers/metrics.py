"""`GET /metrics` for Prometheus.

Request counts and durations come from `RequestContextMiddleware`; the queue
depth is read from Redis at scrape time. Review counts, durations, tokens
and cost are the worker's and are scraped from its own exporter
(`METRICS_PORT`).

Access: with `METRICS_TOKEN` set, a scraper must send it as a bearer token.
Without one the endpoint is open in development and off (404) in
production, so a deployment never exposes it by accident.
"""

import asyncio
import hmac
import logging
from typing import Annotated

from arq import ArqRedis
from arq.constants import default_queue_name
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from observability import metrics

from api.core.config import get_settings
from api.core.queue import get_redis_pool

router = APIRouter(tags=["metrics"], include_in_schema=False)
logger = logging.getLogger(__name__)

QUEUE_TIMEOUT_S = 1.0


def _authorise(authorization: str | None) -> None:
    settings = get_settings()
    token = settings.metrics_token
    if not token:
        if settings.is_production:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
        return
    scheme, _, given = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(given.encode(), token.encode()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "metrics token required",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get("/metrics")
async def prometheus_metrics(
    redis: Annotated[ArqRedis, Depends(get_redis_pool)],
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    _authorise(authorization)
    try:
        depth = await asyncio.wait_for(redis.zcard(default_queue_name), QUEUE_TIMEOUT_S)
        metrics.QUEUE_DEPTH.set(depth)
    except Exception as exc:  # a scrape still answers with what it has
        logger.warning("could not read the queue depth: %s", exc)
    body, content_type = metrics.render()
    return Response(content=body, media_type=content_type)
