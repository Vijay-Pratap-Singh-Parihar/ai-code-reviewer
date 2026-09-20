import uuid

from httpx import AsyncClient


async def _signup_and_get_token(client: AsyncClient, email: str) -> str:
    response = await client.post(
        "/auth/signup",
        json={"org_name": "Acme Inc", "email": email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 201, response.text
    token: str = response.json()["access_token"]
    return token


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _analysis_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "repo_full_name": "acme/widgets",
        "base_branch": "main",
        "head_sha": "abc1234",
        "pr_number": 1,
        "pr_title": "Fix off-by-one",
        "pr_body": "Fixes the loop bound.",
        "diff": (
            "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,3 @@\n"
            "-for i in range(n):\n+for i in range(n + 1):\n"
        ),
    }
    payload.update(overrides)
    return payload


async def test_trigger_analysis_creates_queued_run_and_enqueues_job(
    api_client: AsyncClient,
) -> None:
    token = await _signup_and_get_token(api_client, "trigger@example.com")

    response = await api_client.post(
        "/analysis", json=_analysis_payload(), headers=_auth_header(token)
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["findings"] == []
    assert uuid.UUID(body["id"])

    enqueued = api_client.fake_redis_pool.enqueued  # type: ignore[attr-defined]
    assert len(enqueued) == 1
    function_name, args = enqueued[0]
    assert function_name == "analyze_pr"
    assert args[0] == body["id"]
    assert args[1] == "Fix off-by-one"


async def test_trigger_analysis_requires_auth(api_client: AsyncClient) -> None:
    response = await api_client.post("/analysis", json=_analysis_payload())
    assert response.status_code == 401


async def test_trigger_analysis_rejects_missing_diff(api_client: AsyncClient) -> None:
    token = await _signup_and_get_token(api_client, "baddiff@example.com")

    payload = _analysis_payload()
    payload["diff"] = ""
    response = await api_client.post("/analysis", json=payload, headers=_auth_header(token))

    assert response.status_code == 422


async def test_get_analysis_returns_the_created_run(api_client: AsyncClient) -> None:
    token = await _signup_and_get_token(api_client, "getrun@example.com")

    create_response = await api_client.post(
        "/analysis", json=_analysis_payload(), headers=_auth_header(token)
    )
    run_id = create_response.json()["id"]

    get_response = await api_client.get(f"/analysis/{run_id}", headers=_auth_header(token))

    assert get_response.status_code == 200
    body = get_response.json()
    assert body["id"] == run_id
    assert body["status"] == "queued"


async def test_get_analysis_requires_auth(api_client: AsyncClient) -> None:
    response = await api_client.get(f"/analysis/{uuid.uuid4()}")
    assert response.status_code == 401


async def test_get_analysis_unknown_id_returns_404(api_client: AsyncClient) -> None:
    token = await _signup_and_get_token(api_client, "unknown@example.com")

    response = await api_client.get(f"/analysis/{uuid.uuid4()}", headers=_auth_header(token))
    assert response.status_code == 404


async def test_get_analysis_from_another_org_returns_404(api_client: AsyncClient) -> None:
    """Multi-tenancy enforcement: a run from org A must be invisible to a
    user in org B, even with a valid, unexpired access token.
    """
    token_a = await _signup_and_get_token(api_client, "org-a@example.com")
    create_response = await api_client.post(
        "/analysis", json=_analysis_payload(), headers=_auth_header(token_a)
    )
    run_id = create_response.json()["id"]

    token_b = await _signup_and_get_token(api_client, "org-b@example.com")
    response = await api_client.get(f"/analysis/{run_id}", headers=_auth_header(token_b))

    assert response.status_code == 404


async def test_trigger_analysis_conflicts_when_repo_owned_by_another_org(
    api_client: AsyncClient,
) -> None:
    """Regression test: `repositories.full_name` is globally unique (it
    models a real GitHub repo, which can only belong to one org's App
    installation). A second org posting the same `repo_full_name` must get a
    clear conflict, not silently have their PR attached to the first org's
    repository — which previously made the run invisible to its own creator.
    """
    token_a = await _signup_and_get_token(api_client, "conflict-a@example.com")
    first = await api_client.post(
        "/analysis", json=_analysis_payload(), headers=_auth_header(token_a)
    )
    assert first.status_code == 202

    token_b = await _signup_and_get_token(api_client, "conflict-b@example.com")
    second = await api_client.post(
        "/analysis", json=_analysis_payload(), headers=_auth_header(token_b)
    )

    assert second.status_code == 409
