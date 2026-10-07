"""Local-development webhook relay: smee.io → POST /github/webhook.

GitHub can't reach a laptop, so the App created by the manifest flow
delivers webhooks to a smee.io channel. This process subscribes to that
channel (Server-Sent Events) and replays each delivery to the API with its
original headers, including the HMAC signature the API verifies; the relay
itself is trusted with nothing.

The channel URL is read from the database (`github_app_credentials`), or
`GITHUB_WEBHOOK_PROXY_URL` if set, and re-checked every few seconds, so the
relay can be running before the App exists and picks it up once created.

Run: `python -m api.webhook_relay` (the `webhook-relay` compose service).
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
from db.github import GitHubAppCredentials
from ghapp.relay import SseParser, to_webhook_delivery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.core.config import get_settings

logger = logging.getLogger("revu.webhook_relay")

POLL_SECONDS = 10.0
MAX_BACKOFF_SECONDS = 60.0


async def current_channel(session_factory: async_sessionmaker[AsyncSession]) -> str | None:
    override = os.environ.get("GITHUB_WEBHOOK_PROXY_URL")
    if override:
        return override
    async with session_factory() as session:
        row = await session.get(GitHubAppCredentials, 1)
        return row.webhook_proxy_url if row else None


async def relay_channel(http: httpx.AsyncClient, channel: str, target: str) -> int:
    """Stream one channel until it disconnects; returns deliveries forwarded."""
    forwarded = 0
    parser = SseParser()
    async with http.stream(
        "GET", channel, headers={"Accept": "text/event-stream"}, timeout=httpx.Timeout(None)
    ) as response:
        response.raise_for_status()
        logger.info("relaying %s -> %s", channel, target)
        async for line in response.aiter_lines():
            event = parser.feed(line)
            if event is None:
                continue
            delivery = to_webhook_delivery(event.data)
            if delivery is None:
                continue
            result = await http.post(
                target, content=delivery.body, headers=delivery.headers, timeout=30.0
            )
            forwarded += 1
            logger.info(
                "forwarded %s %s -> %s",
                delivery.headers.get("x-github-event"),
                delivery.headers.get("x-github-delivery"),
                result.status_code,
            )
    return forwarded


async def main() -> None:
    logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(message)s")
    target = os.environ.get("WEBHOOK_RELAY_TARGET", "http://localhost:8000/github/webhook")
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    backoff = 1.0
    try:
        async with httpx.AsyncClient() as http:
            while True:
                try:
                    channel = await current_channel(session_factory)
                except Exception:
                    logger.exception("could not read the relay channel from the database")
                    channel = None
                if not channel:
                    await asyncio.sleep(POLL_SECONDS)
                    continue
                try:
                    await relay_channel(http, channel, target)
                    backoff = 1.0  # clean disconnect: reconnect promptly
                except httpx.HTTPError as exc:
                    logger.warning("relay connection lost (%s); retrying in %.0fs", exc, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
