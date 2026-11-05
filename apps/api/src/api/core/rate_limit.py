"""Rate limits, counted in Redis so they hold across API processes.

Fixed windows: each rule allows `limit` hits per `window_s` seconds for one
key (a client address, a user, an email being logged in to). Going over
answers 429 with `Retry-After`. If Redis can't be reached the request is
let through and a warning logged: a limiter outage must not become an
outage of the whole API.

What is limited, and why:
- sign-in, sign-up and token refresh, per address (and sign-in per email
  too, so guessing one account's password from many addresses is slow);
- anything that spends the organisation's money on a model or calls out to
  a provider (reviews, retries, provider tests), per user.

`REVU_RATE_LIMITS=false` turns them off (the test suite does; individual
tests turn them back on).
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Annotated, Any

from arq import ArqRedis
from fastapi import Depends, HTTPException, Request, status

from api.core.config import get_settings
from api.core.deps import CurrentUser
from api.core.queue import get_redis_pool

logger = logging.getLogger(__name__)

KEY_PREFIX = "ratelimit"


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int
    window_s: int

    def describe(self) -> str:
        minutes = self.window_s // 60
        span = f"{minutes} minute{'s' if minutes != 1 else ''}" if minutes else f"{self.window_s}s"
        return f"{self.limit} per {span}"


SIGNUP = Rule("signup", limit=5, window_s=60 * 60)
LOGIN_ADDRESS = Rule("login-address", limit=20, window_s=5 * 60)
LOGIN_EMAIL = Rule("login-email", limit=10, window_s=15 * 60)
REFRESH = Rule("refresh", limit=60, window_s=5 * 60)
REVIEW = Rule("review", limit=30, window_s=10 * 60)
PROVIDER_TEST = Rule("provider-test", limit=20, window_s=5 * 60)


def client_address(request: Request) -> str:
    """The caller's address as uvicorn saw it. Behind a reverse proxy,
    uvicorn's `--proxy-headers` with `--forwarded-allow-ips` set to the
    proxy makes this the real client rather than the proxy."""
    return request.client.host if request.client else "unknown"


def _key(rule: Rule, subject: str, window: int) -> str:
    # Hashed, so an email or address never sits in Redis in the clear.
    digest = hashlib.sha256(subject.lower().encode()).hexdigest()[:32]
    return f"{KEY_PREFIX}:{rule.name}:{digest}:{window}"


async def hit(redis: ArqRedis, rule: Rule, subject: str) -> None:
    """Count one hit against `rule` for `subject`; raise 429 when over."""
    if not get_settings().revu_rate_limits:
        return
    now = time.time()
    window = int(now // rule.window_s)
    key = _key(rule, subject, window)
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, rule.window_s + 1)
    except Exception as exc:  # fail open: see the module docstring
        logger.warning("rate limiter unavailable, letting the request through: %s", exc)
        return
    if count > rule.limit:
        retry_after = max(1, int((window + 1) * rule.window_s - now))
        logger.warning("rate limit %s exceeded", rule.name, extra={"rule": rule.name})
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"too many requests: the limit is {rule.describe()}; try again in {retry_after}s",
            headers={"Retry-After": str(retry_after)},
        )


def per_address(rule: Rule) -> Any:
    """A route dependency limiting each client address."""

    async def dependency(
        request: Request, redis: Annotated[ArqRedis, Depends(get_redis_pool)]
    ) -> None:
        await hit(redis, rule, client_address(request))

    return Depends(dependency)


def per_user(rule: Rule) -> Any:
    """A route dependency limiting each signed-in user (it authenticates
    the request too, as `CurrentUser` does)."""

    async def dependency(
        user: CurrentUser, redis: Annotated[ArqRedis, Depends(get_redis_pool)]
    ) -> None:
        await hit(redis, rule, str(user.id))

    return Depends(dependency)
