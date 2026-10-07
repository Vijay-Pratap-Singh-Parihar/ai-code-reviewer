"""Test helpers for the API test suite (like `db.testing`).

Give an organisation a review model the way a user would,
through the AI Providers API. Reviews refuse to queue without one."""

from typing import Any

from httpx import AsyncClient

# Never called during these tests (the fake queue doesn't run jobs); it only
# has to pass the endpoint URL checks, which allow loopback outside production.
TEST_BASE_URL = "http://127.0.0.1:11434/v1"
TEST_MODEL = "test-model"


async def configure_review_model(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    created = await client.post(
        "/providers",
        json={"name": "Local test model", "kind": "openai_compatible", "base_url": TEST_BASE_URL},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    provider: dict[str, Any] = created.json()
    routed = await client.put(
        "/providers/routes",
        json={"routes": {"review": {"provider_id": provider["id"], "model": TEST_MODEL}}},
        headers=headers,
    )
    assert routed.status_code == 200, routed.text
    return provider
