import uuid
from datetime import UTC, datetime

from api.db.session import get_db
from api.main import app
from db.branch_index import BranchIndex, BranchIndexStatus
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


async def test_trigger_analysis_defaults_to_diff_only_agent(api_client: AsyncClient) -> None:
    token = await _signup_and_get_token(api_client, "default-agent@example.com")

    response = await api_client.post(
        "/analysis", json=_analysis_payload(), headers=_auth_header(token)
    )

    assert response.status_code == 202, response.text
    enqueued = api_client.fake_redis_pool.enqueued  # type: ignore[attr-defined]
    _function_name, args = enqueued[0]
    assert args[4] == "diff_only"
    assert args[5] is None


async def test_trigger_analysis_cross_file_without_repo_path_returns_422(
    api_client: AsyncClient,
) -> None:
    token = await _signup_and_get_token(api_client, "cross-file-no-path@example.com")

    payload = _analysis_payload(agent="cross_file")
    response = await api_client.post("/analysis", json=payload, headers=_auth_header(token))

    assert response.status_code == 422


async def test_trigger_analysis_cross_file_without_ready_index_returns_409(
    api_client: AsyncClient,
) -> None:
    """The caller's own cost/depth choice (`agent="cross_file"`) must fail
    fast, before any job is enqueued, when Stage 5's branch memory has no
    `ready` build for this (repo, base_branch) yet — there is no on-demand
    indexing fallback.
    """
    token = await _signup_and_get_token(api_client, "cross-file-not-ready@example.com")

    payload = _analysis_payload(agent="cross_file", repo_path="/tmp/acme-widgets")
    response = await api_client.post("/analysis", json=payload, headers=_auth_header(token))

    assert response.status_code == 409
    assert "no ready branch index" in response.json()["detail"]
    assert api_client.fake_redis_pool.enqueued == []  # type: ignore[attr-defined]


async def test_trigger_analysis_cross_file_with_ready_index_enqueues_job(
    api_client: AsyncClient,
) -> None:
    token = await _signup_and_get_token(api_client, "cross-file-ready@example.com")

    # `POST /repos/index` both registers the `Repository` row under this
    # user's org (via `get_or_create_repository`) and hands back its
    # `repo_id` — reused here so the `ready` row seeded below belongs to the
    # exact same repo `/analysis`'s own `get_or_create_repository` call will
    # resolve `repo_full_name` to.
    index_trigger = await api_client.post(
        "/repos/index",
        json={
            "repo_full_name": "acme/widgets",
            "branch_name": "main",
            "repo_path": "/tmp/acme-widgets",
        },
        headers=_auth_header(token),
    )
    assert index_trigger.status_code == 202, index_trigger.text
    repo_id = uuid.UUID(index_trigger.json()["repo_id"])

    # Seed a `ready` BranchIndex row directly through the app's own
    # overridden `get_db` dependency — same pattern `test_branch_index.py`'s
    # staleness test uses — rather than running the real worker build, which
    # is covered separately.
    session_dep = app.dependency_overrides[get_db]
    async for session in session_dep():
        session.add(
            BranchIndex(
                repo_id=repo_id,
                branch_name="main",
                head_sha="a" * 40,
                status=BranchIndexStatus.READY,
                node_count=10,
                edge_count=20,
                graph_ref="/blobs/a.graph.pkl.gz",
                build_duration_ms=500,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
        break

    payload = _analysis_payload(agent="cross_file", repo_path="/tmp/acme-widgets")
    response = await api_client.post("/analysis", json=payload, headers=_auth_header(token))

    assert response.status_code == 202, response.text
    enqueued = api_client.fake_redis_pool.enqueued  # type: ignore[attr-defined]
    # index 0 is the /repos/index trigger's own enqueue; index 1 is /analysis's.
    _function_name, args = enqueued[1]
    assert args[4] == "cross_file"
    assert args[5] == "/tmp/acme-widgets"
