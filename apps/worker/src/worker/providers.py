"""Resolving a run's AI provider into a callable model endpoint.

A queued run records which provider and model it uses (never credentials;
see `api.services.providers.route_snapshot`). Here the worker loads that
provider under the run's organisation (row-level security), decrypts its
key and re-checks its endpoint URL, right before the review runs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from db.ai_endpoints import endpoint_for
from db.provider import AIProvider
from db.pull_request import AnalysisRun
from revu.providers.llm import ModelEndpoint
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ProviderAccess:
    """What the worker needs to use providers: the deployment's credential
    encryption secret and whether private-network endpoints are allowed."""

    secret: str
    allow_private: bool

    def __repr__(self) -> str:  # keep the secret out of logs
        return f"ProviderAccess(allow_private={self.allow_private})"


def provider_access_from_ctx(ctx: dict[str, Any]) -> ProviderAccess:
    access: ProviderAccess = ctx["provider_access"]
    return access


async def endpoint_for_run(
    session: AsyncSession, run: AnalysisRun, access: ProviderAccess
) -> ModelEndpoint:
    snapshot = run.config_snapshot or {}
    provider_id = snapshot.get("provider_id")
    if not provider_id:
        raise RuntimeError(
            "this run has no AI provider recorded (it was queued before AI providers were "
            "configured from the UI); request the review again"
        )
    provider = await session.get(AIProvider, uuid.UUID(str(provider_id)))
    if provider is None:
        raise RuntimeError("the AI provider this review was queued with has been deleted")
    return await endpoint_for(
        provider, str(snapshot["model"]), secret=access.secret, allow_private=access.allow_private
    )
