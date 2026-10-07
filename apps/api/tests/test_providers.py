"""AI Providers API: credentials stay secret, admins manage, tenants are
isolated, endpoints are checked, and the connection test records what the
model can do. No real LLM is called: the engine's `complete` is replaced."""

import json
import uuid
from typing import Any

import httpx
import pytest
from api.core.config import get_settings
from api.core.security import decode_access_token
from api.db.session import get_db
from api.main import app
from api.services import providers as provider_service
from db.credentials import decrypt_credentials
from db.organization import User, UserRole
from db.provider import AIProvider
from db.tenancy import bind_org
from httpx import AsyncClient
from revu.providers.llm import CompletionResult, ToolCall
from sqlalchemy import select

KEY = "gsk_live_abcdefghijklmnop1234"


async def _signup(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/auth/signup",
        json={"org_name": "Acme Inc", "email": email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _create(client: AsyncClient, headers: dict[str, str], **overrides: Any) -> httpx.Response:
    body = {"name": "Groq", "kind": "groq", "api_key": KEY, **overrides}
    return await client.post("/providers", json=body, headers=headers)


async def _row(headers: dict[str, str], provider_id: str) -> AIProvider:
    sessions = app.dependency_overrides[get_db]()
    session = await sessions.__anext__()
    try:
        await bind_org(session, decode_access_token(headers["Authorization"][7:]).org_id)
        row = await session.get(AIProvider, uuid.UUID(provider_id))
        assert row is not None
        return row
    finally:
        await sessions.aclose()


async def _audit(client: AsyncClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = (await client.get("/audit", headers=headers)).json()["entries"]
    return entries


async def test_the_key_goes_in_encrypted_and_never_comes_back_out(api_client: AsyncClient) -> None:
    headers = await _signup(api_client, "keys@example.com")

    created = await _create(api_client, headers)
    listed = await api_client.get("/providers", headers=headers)

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["key_hint"] == "gsk…1234" and body["has_api_key"] is True
    assert KEY not in created.text and KEY not in listed.text
    assert body["effective_base_url"] == "https://api.groq.com/openai/v1"

    row = await _row(headers, body["id"])
    assert KEY not in row.encrypted_credentials
    secret = get_settings().credential_encryption_key
    assert decrypt_credentials(
        secret, row.encrypted_credentials, org_id=row.org_id, provider_id=row.id
    ) == {"api_key": KEY}

    [entry] = [e for e in await _audit(api_client, headers) if e["action"] == "ai_provider.created"]
    assert KEY not in json.dumps(entry)


async def test_members_can_see_providers_but_not_change_them(api_client: AsyncClient) -> None:
    headers = await _signup(api_client, "member-prov@example.com")
    created = (await _create(api_client, headers)).json()
    async for session in app.dependency_overrides[get_db]():
        user = await session.scalar(select(User).where(User.email == "member-prov@example.com"))
        assert user is not None
        user.role = UserRole.MEMBER
        await session.commit()
        break

    listed = await api_client.get("/providers", headers=headers)
    responses = [
        await _create(api_client, headers, name="Another"),
        await api_client.patch(f"/providers/{created['id']}", json={"name": "x"}, headers=headers),
        await api_client.delete(f"/providers/{created['id']}", headers=headers),
        await api_client.post(
            f"/providers/{created['id']}/test", json={"model": "m"}, headers=headers
        ),
        await api_client.put("/providers/routes", json={"routes": {}}, headers=headers),
    ]

    assert listed.status_code == 200 and listed.json()[0]["name"] == "Groq"
    assert [r.status_code for r in responses] == [403] * 5


async def test_another_organisation_cannot_see_or_touch_a_provider(api_client: AsyncClient) -> None:
    owner = await _signup(api_client, "prov-owner@example.com")
    provider = (await _create(api_client, owner)).json()
    other = await _signup(api_client, "prov-other@example.com")

    assert (await api_client.get("/providers", headers=other)).json() == []
    patched = await api_client.patch(
        f"/providers/{provider['id']}", json={"name": "mine now"}, headers=other
    )
    routed = await api_client.put(
        "/providers/routes",
        json={"routes": {"review": {"provider_id": provider["id"], "model": "m"}}},
        headers=other,
    )
    assert patched.status_code == 404
    assert routed.status_code == 422  # "unknown provider", not someone else's


async def test_rotating_the_key_resets_the_test_and_keeping_it_needs_no_resubmission(
    api_client: AsyncClient,
) -> None:
    headers = await _signup(api_client, "rotate@example.com")
    provider = (await _create(api_client, headers)).json()

    renamed = await api_client.patch(
        f"/providers/{provider['id']}", json={"name": "Groq (team)"}, headers=headers
    )
    rotated = await api_client.patch(
        f"/providers/{provider['id']}", json={"api_key": "gsk_rotated_key_9999"}, headers=headers
    )

    assert renamed.json()["key_hint"] == "gsk…1234"  # untouched key kept
    assert rotated.json()["key_hint"] == "gsk…9999"
    assert rotated.json()["verified_at"] is None
    updates = [e for e in await _audit(api_client, headers) if e["action"] == "ai_provider.updated"]
    assert [e["metadata"]["changed"] for e in updates] == [["api_key"], ["name"]]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"kind": "openai_compatible", "base_url": "http://169.254.169.254/v1"}, "link-local"),
        ({"kind": "openai_compatible", "base_url": None}, "needs an endpoint URL"),
        ({"kind": "openai_compatible", "base_url": "ftp://models.local/v1"}, "http"),
        ({"kind": "anthropic", "api_key": None}, "needs an API key"),
        ({"kind": "bedrock"}, "isn't supported yet"),
    ],
)
async def test_unusable_configurations_are_refused(
    api_client: AsyncClient, overrides: dict[str, Any], message: str
) -> None:
    headers = await _signup(api_client, f"bad-{uuid.uuid4().hex[:6]}@example.com")

    response = await _create(api_client, headers, **overrides)

    assert response.status_code == 422
    assert message in response.json()["detail"]


async def test_private_endpoints_are_refused_in_production_unless_allowed(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _signup(api_client, "prod@example.com")
    local = {"kind": "openai_compatible", "base_url": "http://127.0.0.1:11434/v1", "name": "Local"}
    monkeypatch.setattr(get_settings(), "environment", "production")
    monkeypatch.setattr(get_settings(), "credential_encryption_key", "a-real-production-secret")

    refused = await _create(api_client, headers, **local)
    monkeypatch.setattr(get_settings(), "revu_allow_private_provider_urls", True)
    allowed = await _create(api_client, headers, **local)

    assert refused.status_code == 422 and "private" in refused.json()["detail"]
    assert allowed.status_code == 201


async def test_names_are_unique_per_organisation(api_client: AsyncClient) -> None:
    headers = await _signup(api_client, "names@example.com")
    await _create(api_client, headers)

    duplicate = await _create(api_client, headers)

    assert duplicate.status_code == 409


async def test_routes_choose_a_provider_and_model_per_step_and_deletion_removes_them(
    api_client: AsyncClient,
) -> None:
    headers = await _signup(api_client, "routes@example.com")
    provider = (await _create(api_client, headers)).json()

    put = await api_client.put(
        "/providers/routes",
        json={
            "routes": {
                "review": {"provider_id": provider["id"], "model": "openai/gpt-oss-120b"},
                "screen": {"provider_id": provider["id"], "model": "openai/gpt-oss-20b"},
            }
        },
        headers=headers,
    )
    listed = (await api_client.get("/providers", headers=headers)).json()
    cleared = await api_client.put(
        "/providers/routes", json={"routes": {"screen": None}}, headers=headers
    )
    deleted = await api_client.delete(f"/providers/{provider['id']}", headers=headers)
    after = (await api_client.get("/providers/routes", headers=headers)).json()

    assert [(r["tier"], r["model"]) for r in put.json()] == [
        ("screen", "openai/gpt-oss-20b"),
        ("review", "openai/gpt-oss-120b"),
    ]
    assert listed[0]["used_by"] == ["screen", "review"]
    assert [r["tier"] for r in cleared.json()] == ["review"]
    assert deleted.status_code == 204 and after == []
    [entry] = [e for e in await _audit(api_client, headers) if e["action"] == "ai_provider.deleted"]
    assert entry["metadata"]["routes_removed"] == ["review"]


def _fake_complete(*, json_ok: bool = True, calls_tool: bool = True, fail: bool = False) -> Any:
    async def fake(
        *, model: Any, messages: list[dict[str, Any]], **kwargs: Any
    ) -> CompletionResult:
        if fail:
            raise RuntimeError("connection refused")
        if kwargs.get("tools"):
            calls = (
                [
                    ToolCall(
                        id="1", name="get_line_count", arguments={"path": "x"}, raw_arguments="{}"
                    )
                ]
                if calls_tool
                else []
            )
            return CompletionResult("", 5, 5, 0.0001, 3, tool_calls=calls)
        if kwargs.get("response_format"):
            return CompletionResult('{"ok": true}' if json_ok else "sure!", 5, 5, 0.0001, 3)
        return CompletionResult("OK", 5, 1, 0.0001, 3)

    return fake


async def test_connection_test_records_what_the_model_can_do(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _signup(api_client, "probe@example.com")
    provider = (await _create(api_client, headers)).json()
    monkeypatch.setattr(provider_service, "complete", _fake_complete(calls_tool=False))

    result = await api_client.post(
        f"/providers/{provider['id']}/test", json={"model": "openai/gpt-oss-20b"}, headers=headers
    )
    after = (await api_client.get("/providers", headers=headers)).json()[0]

    assert result.status_code == 200, result.text
    body = result.json()
    assert body["ok"] is True and body["reply"]["detail"] == "OK"
    assert body["json_mode"]["ok"] is True and body["tool_calling"]["ok"] is False
    assert after["settings"]["supports_json"] is True
    assert after["settings"]["supports_tools"] is False
    assert after["verified_at"] is not None and after["last_test_error"] is None


async def test_a_failed_connection_test_is_reported_not_raised(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _signup(api_client, "probe-fail@example.com")
    provider = (await _create(api_client, headers)).json()
    monkeypatch.setattr(provider_service, "complete", _fake_complete(fail=True))

    result = (
        await api_client.post(
            f"/providers/{provider['id']}/test", json={"model": "m"}, headers=headers
        )
    ).json()
    after = (await api_client.get("/providers", headers=headers)).json()[0]

    assert result["ok"] is False
    assert "connection refused" in result["reply"]["detail"]
    assert result["json_mode"]["detail"].startswith("skipped")
    assert "connection refused" in after["last_test_error"]


async def test_model_list_is_read_from_the_provider(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _signup(api_client, "models@example.com")
    provider = (await _create(api_client, headers)).json()
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json={"data": [{"id": "openai/gpt-oss-120b"}, {"id": "qwen3"}]})

    async def no_dns(url: str, *, allow_private: bool) -> None:
        return None  # keep the test off the network; URL checks have their own tests

    monkeypatch.setattr(provider_service, "check_endpoint_url_async", no_dns)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        provider_service.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )

    response = await api_client.get(f"/providers/{provider['id']}/models", headers=headers)

    assert response.json() == {"models": ["openai/gpt-oss-120b", "qwen3"]}
    assert seen["url"] == "https://api.groq.com/openai/v1/models"
    assert seen["auth"] == f"Bearer {KEY}"
