"""Real-Postgres, real-FastAPI-app tests for the branch-memory endpoints,
same `api_client` pattern as `test_analysis.py`.

Builds are queued through `POST /repos/{repo_id}/index` for GitHub-connected
repos (covered in `test_github.py`); these tests prove the status endpoint's
staleness logic never exposes a `building` row's data, and its org scoping.
The worker's own state-machine behaviour (status transitions, force-push
detection, incremental vs. full) is covered by
`apps/worker/tests/test_index_branch.py` against a real git repo.
"""

import uuid
from datetime import UTC, datetime, timedelta

from api.core.security import decode_access_token
from api.db.session import get_db
from api.main import app
from db.branch_index import BranchIndex, BranchIndexStatus
from db.tenancy import bind_org
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


async def _create_repo(client: AsyncClient, token: str) -> str:
    """Registers `acme/widgets` under the caller's org through the manual
    diff-only flow and returns its id."""
    response = await client.post(
        "/analysis",
        json={
            "repo_full_name": "acme/widgets",
            "head_sha": "abc1234",
            "pr_number": 1,
            "pr_title": "t",
            "diff": "--- a/x\n+++ b/x\n",
        },
        headers=_auth_header(token),
    )
    assert response.status_code == 202, response.text
    repos = await client.get("/repos", headers=_auth_header(token))
    repo_id: str = repos.json()[0]["id"]
    return repo_id


async def test_manual_index_endpoint_with_worker_path_is_gone(api_client: AsyncClient) -> None:
    """Regression: `POST /repos/index` took a `repo_path` on the worker's
    filesystem, letting any user make the worker read any directory."""
    token = await _signup_and_get_token(api_client, "manual-index@example.com")
    response = await api_client.post(
        "/repos/index",
        json={"repo_full_name": "acme/widgets", "repo_path": "/data/repos"},
        headers=_auth_header(token),
    )
    assert response.status_code in (404, 405)
    assert api_client.fake_redis_pool.enqueued == []  # type: ignore[attr-defined]


async def test_get_branch_index_reports_no_index_yet(api_client: AsyncClient) -> None:
    token = await _signup_and_get_token(api_client, "status-none@example.com")
    repo_id = await _create_repo(api_client, token)

    response = await api_client.get(
        f"/repos/{repo_id}/branches/main/index", headers=_auth_header(token)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["has_ready_index"] is False
    assert body["head_sha"] is None
    assert body["latest_attempt_status"] is None
    assert body["is_stale"] is False


async def test_get_branch_index_requires_auth(api_client: AsyncClient) -> None:
    response = await api_client.get(f"/repos/{uuid.uuid4()}/branches/main/index")
    assert response.status_code == 401


async def test_get_branch_index_unknown_repo_returns_404(api_client: AsyncClient) -> None:
    token = await _signup_and_get_token(api_client, "status-404@example.com")
    response = await api_client.get(
        f"/repos/{uuid.uuid4()}/branches/main/index", headers=_auth_header(token)
    )
    assert response.status_code == 404


async def test_get_branch_index_from_another_org_returns_404(api_client: AsyncClient) -> None:
    token_a = await _signup_and_get_token(api_client, "status-org-a@example.com")
    repo_id = await _create_repo(api_client, token_a)

    token_b = await _signup_and_get_token(api_client, "status-org-b@example.com")
    response = await api_client.get(
        f"/repos/{repo_id}/branches/main/index", headers=_auth_header(token_b)
    )
    assert response.status_code == 404


async def test_get_branch_index_returns_ready_row_and_flags_stale_during_rebuild(
    api_client: AsyncClient,
) -> None:
    """The core staleness-as-first-class-state assertion: once a `ready` row
    exists, a concurrent `building` attempt must never change what the
    status endpoint reports as the usable snapshot — only `is_stale` flips.

    Manipulates rows directly through the API app's own overridden `get_db`
    dependency (the same session-per-request machinery `api_client` itself
    drives, per `conftest.py`) rather than the worker, so this test can
    construct the exact "one ready, one building" situation deterministically
    without depending on the worker's own build logic (covered separately in
    `apps/worker/tests/test_index_branch.py`).
    """
    token = await _signup_and_get_token(api_client, "staleness@example.com")
    repo_id = uuid.UUID(await _create_repo(api_client, token))

    # `created_at` is set explicitly (not left to the DB's `now()`): Postgres's
    # `now()` is transaction-scoped, and this whole test runs inside one
    # savepoint-wrapped transaction (per `api_client`'s fixture), so rows
    # inserted here would otherwise all get an identical timestamp — which is
    # exactly the ordering this test needs to be unambiguous about. Explicit,
    # clearly-ordered timestamps make the "which row is newer" intent visible
    # in the test itself rather than relying on wall-clock timing.
    t0 = datetime.now(UTC)
    ready_row = BranchIndex(
        repo_id=repo_id,
        branch_name="main",
        head_sha="a" * 40,
        status=BranchIndexStatus.READY,
        node_count=10,
        edge_count=20,
        graph_ref="/blobs/a.graph.pkl.gz",
        unresolved_symbols=[{"kind": "call", "reason": "test"}],
        build_duration_ms=500,
        created_at=t0,
    )
    session_dep = app.dependency_overrides[get_db]
    async for session in session_dep():
        await bind_org(session, decode_access_token(token).org_id)
        session.add(ready_row)
        await session.commit()

        # A *new* row, inserted after `ready_row` succeeded, simulating a
        # rebuild that started later.
        building_row = BranchIndex(
            repo_id=repo_id,
            branch_name="main",
            head_sha="b" * 40,
            status=BranchIndexStatus.BUILDING,
            created_at=t0 + timedelta(seconds=1),
        )
        session.add(building_row)
        await session.commit()
        break

    response = await api_client.get(
        f"/repos/{repo_id}/branches/main/index", headers=_auth_header(token)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["has_ready_index"] is True
    assert body["head_sha"] == "a" * 40  # the ready row's data, never the building row's
    assert body["node_count"] == 10
    assert body["edge_count"] == 20
    assert body["unresolved_count"] == 1
    assert body["is_stale"] is True
    assert body["latest_attempt_status"] == "building"
