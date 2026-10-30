"""Test helpers for the worker test suite (like `db.testing`).

Creates a real AI provider row, with credentials encrypted exactly as the
API stores them, and returns the snapshot a queued run would carry, so
worker tests exercise the real provider resolution and decryption path.
"""

from __future__ import annotations

import uuid
from typing import Any

from db.credentials import encrypt_credentials
from db.provider import AIProvider, ProviderKind
from sqlalchemy.ext.asyncio import AsyncSession

from worker.providers import ProviderAccess

TEST_SECRET = "test-credential-secret"
TEST_ACCESS = ProviderAccess(secret=TEST_SECRET, allow_private=True)


async def review_provider(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    model: str = "test-model",
    supports_tools: bool | None = None,
    api_key: str | None = "sk-test-key",
) -> dict[str, Any]:
    """An OpenAI-compatible provider for `org_id` (the session must already
    act for that organisation) and the run snapshot that points at it."""
    provider = AIProvider(
        id=uuid.uuid4(),
        org_id=org_id,
        name=f"Test provider {uuid.uuid4().hex[:6]}",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:11434/v1",
        settings={} if supports_tools is None else {"supports_tools": supports_tools},
        encrypted_credentials="",
    )
    provider.encrypted_credentials = encrypt_credentials(
        TEST_SECRET,
        {"api_key": api_key} if api_key else {},
        org_id=org_id,
        provider_id=provider.id,
    )
    session.add(provider)
    await session.flush()
    return {
        "provider_id": str(provider.id),
        "provider_name": provider.name,
        "provider_kind": str(provider.kind),
        "model": model,
    }
