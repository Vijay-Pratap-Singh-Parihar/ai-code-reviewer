"""Benchmark systems that use a provider configured on revu's AI Providers
screen, so a key pasted into the UI never has to be exported to a shell.

A system names the organisation and the provider (`"org": "Acme",
"provider": "Groq"`) and the model id as typed in the UI. The provider is
read under that organisation's row-level security and its key decrypted
exactly as the worker does it (`db.ai_endpoints.endpoint_for`), using the
deployment's `DATABASE_URL` and `CREDENTIAL_ENCRYPTION_KEY`.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from db.ai_endpoints import endpoint_for
from db.organization import Organization
from db.provider import AIProvider
from db.tenancy import bind_org
from revu.providers.llm import ModelEndpoint
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from revu_bench.runner import SystemConfig

# The worker's development default; any real deployment sets its own.
DEV_SECRET = "change-me-dev-only-not-for-production"


class ProviderLookupError(RuntimeError):
    pass


async def resolve_ui_providers(
    systems: Sequence[SystemConfig],
    *,
    database_url: str | None = None,
    secret: str | None = None,
    allow_private: bool = True,
) -> dict[str, ModelEndpoint]:
    """The endpoint for every system that names a UI provider, by system name."""
    wanted = [s for s in systems if s.provider]
    if not wanted:
        return {}
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise ProviderLookupError("set DATABASE_URL to use providers configured in the UI")
    secret = secret or os.environ.get("CREDENTIAL_ENCRYPTION_KEY") or DEV_SECRET
    engine = create_async_engine(url)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        endpoints: dict[str, ModelEndpoint] = {}
        for system in wanted:
            async with sessions() as session:
                endpoints[system.name] = await _endpoint(session, system, secret, allow_private)
        return endpoints
    finally:
        await engine.dispose()


async def _endpoint(
    session: AsyncSession, system: SystemConfig, secret: str, allow_private: bool
) -> ModelEndpoint:
    orgs = (
        await session.scalars(select(Organization).where(Organization.name == system.org))
    ).all()
    if len(orgs) != 1:
        found = "no" if not orgs else f"{len(orgs)}"
        raise ProviderLookupError(f"{system.name}: {found} organizations named {system.org!r}")
    await bind_org(session, orgs[0].id)
    provider = await session.scalar(select(AIProvider).where(AIProvider.name == system.provider))
    if provider is None:
        raise ProviderLookupError(
            f"{system.name}: {system.org!r} has no AI provider named {system.provider!r}"
        )
    return await endpoint_for(
        provider,
        system.model,
        secret=secret,
        allow_private=allow_private,
        reasoning=system.reasoning,
    )
