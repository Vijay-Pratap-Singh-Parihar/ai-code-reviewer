"""Real-Postgres, real-FastAPI-app tests for the branch-memory endpoints,
same `api_client` pattern as `test_analysis.py`.

The worker job itself (`worker.jobs.index_branch.update_branch_index`) is
not exercised here — Redis is faked (`FakeRedisPool`, from `conftest.py`),
same as `test_analysis.py`'s enqueue assertions — so these tests only prove
the API/service-layer row lifecycle: trigger creates a `pending` row and
enqueues the right job, and the status endpoint's staleness logic never
exposes a `building` row's data. The worker's own state-machine behaviour
(status transitions, force-push detection, incremental vs. full) is covered
by `apps/worker/tests/test_index_branch.py` against a real git repo.
"""

import uuid
from datetime import UTC, datetime, timedelta

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


def _trigger_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "repo_full_name": "acme/widgets",
        "branch_name": "main",
        "repo_path": "/tmp/acme-widgets",
    }
    payload.update(overrides)
    return payload


async def test_trigger_index_creates_pending_row_and_enqueues_job(api_client: AsyncClient) -> None:
    token = await _signup_and_get_token(api_client, "trigger-index@example.com")

    response = await api_client.post(
        "/repos/index", json=_trigger_payload(), headers=_auth_header(token)
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["branch_name"] == "main"
    assert body["head_sha"] is None
    assert uuid.UUID(body["id"])
    assert uuid.UUID(body["repo_id"])

    enqueued = api_client.fake_redis_pool.enqueued  # type: ignore[attr-defined]
    assert len(enqueued) == 1
    function_name, args = enqueued[0]
    assert function_name == "update_branch_index"
    assert args[0] == body["id"]
    assert args[1] == "/tmp/acme-widgets"
    assert args[2] is None  # no target_sha pinned
    assert args[3] is False  # force_full defaults to False


async def test_trigger_index_passes_through_target_sha_and_force_full(
    api_client: AsyncClient,
) -> None:
    token = await _signup_and_get_token(api_client, "trigger-index-2@example.com")

    response = await api_client.post(
        "/repos/index",
        json=_trigger_payload(target_sha="a" * 40, force_full=True),
        headers=_auth_header(token),
    )

    assert response.status_code == 202, response.text
    enqueued = api_client.fake_redis_pool.enqueued  # type: ignore[attr-defined]
    _function_name, args = enqueued[0]
    assert args[2] == "a" * 40
    assert args[3] is True


async def test_trigger_index_requires_auth(api_client: AsyncClient) -> None:
    response = await api_client.post("/repos/index", json=_trigger_payload())
    assert response.status_code == 401


async def test_trigger_index_rejects_missing_repo_path(api_client: AsyncClient) -> None:
    token = await _signup_and_get_token(api_client, "bad-index-payload@example.com")
    payload = _trigger_payload()
    payload["repo_path"] = ""
    response = await api_client.post("/repos/index", json=payload, headers=_auth_header(token))
    assert response.status_code == 422


async def test_trigger_index_conflicts_when_repo_owned_by_another_org(
    api_client: AsyncClient,
) -> None:
    token_a = await _signup_and_get_token(api_client, "index-conflict-a@example.com")
    first = await api_client.post(
        "/repos/index", json=_trigger_payload(), headers=_auth_header(token_a)
    )
    assert first.status_code == 202

    token_b = await _signup_and_get_token(api_client, "index-conflict-b@example.com")
    second = await api_client.post(
        "/repos/index", json=_trigger_payload(), headers=_auth_header(token_b)
    )
    assert second.status_code == 409


async def test_get_branch_index_reports_no_index_yet_before_any_build_completes(
    api_client: AsyncClient,
) -> None:
    token = await _signup_and_get_token(api_client, "status-none@example.com")
    trigger = await api_client.post(
        "/repos/index", json=_trigger_payload(), headers=_auth_header(token)
    )
    repo_id = trigger.json()["repo_id"]

    response = await api_client.get(
        f"/repos/{repo_id}/branches/main/index", headers=_auth_header(token)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["has_ready_index"] is False
    assert body["head_sha"] is None
    assert body["latest_attempt_status"] == "pending"
    assert body["is_stale"] is False  # nothing to be stale relative to yet


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
    trigger = await api_client.post(
        "/repos/index", json=_trigger_payload(), headers=_auth_header(token_a)
    )
    repo_id = trigger.json()["repo_id"]

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
    trigger = await api_client.post(
        "/repos/index", json=_trigger_payload(), headers=_auth_header(token)
    )
    repo_id = uuid.UUID(trigger.json()["repo_id"])

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
        session.add(ready_row)
        await session.commit()

        # A *new* row, inserted after `ready_row` succeeded, simulating a
        # rebuild that started later (the originally-triggered `pending` row
        # from before `ready_row` existed is older and must stay irrelevant).
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
