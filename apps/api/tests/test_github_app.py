"""One-click GitHub App creation (manifest flow), end to end through the API.

GitHub's conversion endpoint and smee.io are monkeypatched at the service
module's import site; everything else (state signing, encryption, the
database row, webhook verification with the stored secret) is real.
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from api.core.github import resolve_app
from api.db.session import get_db
from api.main import app
from api.services import github_app as github_app_service
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from db.github import GitHubAppCredentials
from db.organization import User, UserRole
from ghapp import sign_payload
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

SMEE = "https://smee.io/TestChannel123"


def _pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


PEM = _pem()
CREATED = {
    "id": 123456,
    "slug": "revu-test",
    "name": "revu-test",
    "owner": {"login": "vijay", "type": "User"},
    "html_url": "https://github.com/apps/revu-test",
    "client_id": "Iv23liTEST",
    "client_secret": "client-secret-value",
    "webhook_secret": "webhook-secret-value",
    "pem": PEM,
}


@pytest.fixture
def fake_github_app(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"smee": 0, "codes": []}

    async def fake_smee(**_: object) -> str:
        calls["smee"] += 1
        return SMEE

    async def fake_convert(code: str, **_: object) -> dict[str, Any]:
        calls["codes"].append(code)
        return CREATED

    monkeypatch.setattr(github_app_service, "create_smee_channel", fake_smee)
    monkeypatch.setattr(github_app_service, "convert_manifest_code", fake_convert)
    return calls


async def _session() -> AsyncIterator[AsyncSession]:
    """A session on the api_client fixture's rolled-back test transaction."""
    async for session in app.dependency_overrides[get_db]():
        yield session


async def _signup(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/auth/signup",
        json={"org_name": "Acme Inc", "email": email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _start(client: AsyncClient, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    response = await client.post("/github/app/manifest", json=body, headers=headers)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def _state(action_url: str) -> str:
    return parse_qs(urlparse(action_url).query)["state"][0]


async def test_manifest_start_builds_a_read_only_app_with_a_smee_webhook(
    api_client: AsyncClient, fake_github_app: dict[str, Any]
) -> None:
    headers = await _signup(api_client, "start@example.com")

    started = await _start(api_client, headers)

    assert started["action_url"].startswith("https://github.com/settings/apps/new?state=")
    manifest = started["manifest"]
    assert manifest["hook_attributes"]["url"] == SMEE
    assert manifest["redirect_url"] == "http://localhost:3000/github/app-created"
    assert manifest["default_permissions"] == {
        "contents": "read",
        "pull_requests": "read",
        "metadata": "read",
    }
    assert fake_github_app["smee"] == 1


async def test_manifest_start_for_an_organization(
    api_client: AsyncClient, fake_github_app: dict[str, Any]
) -> None:
    headers = await _signup(api_client, "org@example.com")

    started = await _start(api_client, headers, organization="acme-inc")
    bad = await api_client.post(
        "/github/app/manifest", json={"organization": "../x"}, headers=headers
    )

    assert "/organizations/acme-inc/settings/apps/new?" in started["action_url"]
    assert bad.status_code == 422


async def test_full_flow_stores_encrypted_credentials_and_configures_the_app(
    api_client: AsyncClient, fake_github_app: dict[str, Any]
) -> None:
    headers = await _signup(api_client, "full@example.com")
    before = (await api_client.get("/github/app", headers=headers)).json()
    started = await _start(api_client, headers)

    created = await api_client.post(
        "/github/app/conversions",
        json={"code": "one-time-code", "state": _state(started["action_url"])},
        headers=headers,
    )

    assert before["configured"] is False
    assert created.status_code == 201, created.text
    info = created.json()
    assert info["configured"] is True
    assert info["source"] == "database"
    assert info["install_url"] == "https://github.com/apps/revu-test/installations/new"
    assert info["webhook_configured"] is True
    assert info["webhook_proxy_url"] == SMEE
    assert fake_github_app["codes"] == ["one-time-code"]

    async for session in _session():
        row = await session.get(GitHubAppCredentials, 1)
        assert row is not None
        stored = " ".join([row.private_key_enc, row.client_secret_enc, row.webhook_secret_enc])
        for secret in ("PRIVATE KEY", "client-secret-value", "webhook-secret-value"):
            assert secret not in stored
        resolved = await resolve_app(session)
        assert resolved is not None
        assert resolved.config.app_id == "123456"
        assert resolved.config.private_key_pem == PEM
        assert resolved.config.client_secret == "client-secret-value"

    # Exactly one App per deployment.
    again = await api_client.post("/github/app/manifest", json={}, headers=headers)
    assert again.status_code == 409


async def test_stored_webhook_secret_verifies_deliveries(
    api_client: AsyncClient, fake_github_app: dict[str, Any]
) -> None:
    headers = await _signup(api_client, "hook@example.com")
    started = await _start(api_client, headers)
    await api_client.post(
        "/github/app/conversions",
        json={"code": "c", "state": _state(started["action_url"])},
        headers=headers,
    )
    body = json.dumps({"zen": "hi"}).encode()

    async def send(secret: str) -> int:
        response = await api_client.post(
            "/github/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "ping",
                "X-GitHub-Delivery": str(uuid.uuid4()),
                "X-Hub-Signature-256": sign_payload(secret, body),
            },
        )
        return response.status_code

    assert await send("webhook-secret-value") == 200
    assert await send("guessed-secret") == 401


async def test_state_is_bound_to_the_user_who_started(
    api_client: AsyncClient, fake_github_app: dict[str, Any]
) -> None:
    starter = await _signup(api_client, "starter@example.com")
    other = await _signup(api_client, "other@example.com")
    state = _state((await _start(api_client, starter))["action_url"])

    stolen = await api_client.post(
        "/github/app/conversions", json={"code": "c", "state": state}, headers=other
    )
    forged = await api_client.post(
        "/github/app/conversions", json={"code": "c", "state": state[:-4] + "AAAA"}, headers=starter
    )

    assert stolen.status_code == 400
    assert forged.status_code == 400
    assert fake_github_app["codes"] == []  # GitHub was never called


async def test_members_cannot_create_the_app(
    api_client: AsyncClient, fake_github_app: dict[str, Any]
) -> None:
    headers = await _signup(api_client, "member@example.com")
    async for session in _session():
        user = await session.scalar(select(User).where(User.email == "member@example.com"))
        assert user is not None
        user.role = UserRole.MEMBER
        await session.commit()

    response = await api_client.post("/github/app/manifest", json={}, headers=headers)

    assert response.status_code == 403
    assert fake_github_app["smee"] == 0
