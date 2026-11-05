"""POST /analysis/{id}/retry: a failed review runs again as a new run."""

import uuid
from typing import Any

from api.core.security import decode_access_token
from api.db.session import get_db
from api.main import app
from api.testing import configure_review_model
from db.ledger import AuditLog
from db.pull_request import AnalysisRun, AnalysisRunStatus
from db.tenancy import bind_org
from httpx import AsyncClient
from sqlalchemy import select


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _signup_and_get_token(client: AsyncClient, email: str) -> str:
    response = await client.post(
        "/auth/signup",
        json={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "org_name": "Acme Inc",
            "email": email,
            "password": "correct-horse-battery",
        },
    )
    assert response.status_code == 201, response.text
    token: str = response.json()["access_token"]
    await configure_review_model(client, _auth_header(token))
    return token


def _analysis_payload() -> dict[str, object]:
    return {
        "repo_full_name": "acme/widgets",
        "base_branch": "main",
        "head_sha": "abc1234",
        "pr_number": 1,
        "pr_title": "Fix off-by-one",
        "pr_body": "Fixes the loop bound.",
        "diff": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n",
    }


async def _queued_run(client: AsyncClient, email: str) -> tuple[str, str]:
    token = await _signup_and_get_token(client, email)
    created = await client.post("/analysis", json=_analysis_payload(), headers=_auth_header(token))
    assert created.status_code == 202, created.text
    return token, created.json()["id"]


async def _edit_run(token: str, run_id: str, **changes: Any) -> None:
    session_dep = app.dependency_overrides[get_db]
    async for session in session_dep():
        await bind_org(session, decode_access_token(token).org_id)
        run = await session.get(AnalysisRun, uuid.UUID(run_id))
        assert run is not None
        snapshot = changes.pop("config_snapshot", None)
        if snapshot:
            run.config_snapshot = {**run.config_snapshot, **snapshot}
        for key, value in changes.items():
            setattr(run, key, value)
        await session.commit()
        break


async def _fail(token: str, run_id: str, **snapshot: Any) -> None:
    await _edit_run(
        token,
        run_id,
        status=AnalysisRunStatus.FAILED,
        error="The AI provider could not be reached.",
        config_snapshot=snapshot,
    )


async def test_a_failed_review_is_queued_again_as_a_new_run(api_client: AsyncClient) -> None:
    token, run_id = await _queued_run(api_client, "retry@example.com")
    await _fail(token, run_id)
    api_client.fake_redis_pool.enqueued.clear()  # type: ignore[attr-defined]

    response = await api_client.post(f"/analysis/{run_id}/retry", headers=_auth_header(token))

    assert response.status_code == 202, response.text
    retry = response.json()
    assert retry["id"] != run_id
    assert retry["status"] == "queued"
    assert retry["retry_of"] == run_id
    assert retry["pr_title"] == "Fix off-by-one"
    enqueued = api_client.fake_redis_pool.enqueued  # type: ignore[attr-defined]
    assert enqueued == [("analyze_pr", (retry["id"], str(decode_access_token(token).org_id)))]

    # The failure is kept, as it was.
    original = (await api_client.get(f"/analysis/{run_id}", headers=_auth_header(token))).json()
    assert original["status"] == "failed"
    assert original["error"] == "The AI provider could not be reached."
    again = (await api_client.get(f"/analysis/{retry['id']}", headers=_auth_header(token))).json()
    assert again["diff"] == original["diff"]


async def test_the_retry_is_in_the_audit_log(api_client: AsyncClient) -> None:
    token, run_id = await _queued_run(api_client, "retry-audit@example.com")
    await _fail(token, run_id)

    retry = (await api_client.post(f"/analysis/{run_id}/retry", headers=_auth_header(token))).json()

    session_dep = app.dependency_overrides[get_db]
    async for session in session_dep():
        await bind_org(session, decode_access_token(token).org_id)
        entries = list(await session.scalars(select(AuditLog)))
        break
    retries = [e for e in entries if (e.extra or {}).get("trigger") == "retry"]
    assert len(retries) == 1
    assert retries[0].extra["run_id"] == retry["id"]
    assert retries[0].extra["retry_of"] == run_id


async def test_only_a_failed_review_can_be_retried(api_client: AsyncClient) -> None:
    token, run_id = await _queued_run(api_client, "retry-queued@example.com")

    queued = await api_client.post(f"/analysis/{run_id}/retry", headers=_auth_header(token))
    await _edit_run(token, run_id, status=AnalysisRunStatus.SUCCEEDED)
    succeeded = await api_client.post(f"/analysis/{run_id}/retry", headers=_auth_header(token))

    assert queued.status_code == 409
    assert succeeded.status_code == 409
    assert "only a failed review" in succeeded.json()["detail"]


async def test_another_organisations_run_cannot_be_retried(api_client: AsyncClient) -> None:
    owner, run_id = await _queued_run(api_client, "retry-owner@example.com")
    await _fail(owner, run_id)
    other = await _signup_and_get_token(api_client, "retry-other@example.com")

    response = await api_client.post(f"/analysis/{run_id}/retry", headers=_auth_header(other))

    assert response.status_code == 404


async def test_retry_needs_auth(api_client: AsyncClient) -> None:
    response = await api_client.post(f"/analysis/{uuid.uuid4()}/retry")
    assert response.status_code == 401


async def test_a_github_review_is_retried_through_the_github_job(
    api_client: AsyncClient,
) -> None:
    """A pull request review re-fetches the PR from GitHub, so its retry
    goes back through `review_github_pr`, not the pasted-diff job."""
    token, run_id = await _queued_run(api_client, "retry-github@example.com")
    await _fail(token, run_id, source="github", trigger="webhook")
    api_client.fake_redis_pool.enqueued.clear()  # type: ignore[attr-defined]

    response = await api_client.post(f"/analysis/{run_id}/retry", headers=_auth_header(token))

    assert response.status_code == 202, response.text
    retry = response.json()
    assert retry["source"] == "github"
    assert retry["retry_of"] == run_id
    enqueued = api_client.fake_redis_pool.enqueued  # type: ignore[attr-defined]
    assert [name for name, _ in enqueued] == ["review_github_pr"]


async def test_run_anyway_retries_a_review_stopped_at_the_token_ceiling(
    api_client: AsyncClient,
) -> None:
    token, run_id = await _queued_run(api_client, "ceiling@example.com")
    await _edit_run(
        token,
        run_id,
        status=AnalysisRunStatus.FAILED,
        error="This review would use about 90,000 tokens, over this repository's limit.",
        estimated_tokens=90000,
        config_snapshot={"over_ceiling": {"estimate": 90000, "ceiling": 50000}},
    )
    stopped = (await api_client.get(f"/analysis/{run_id}", headers=_auth_header(token))).json()
    assert stopped["over_ceiling"] is True and stopped["estimated_tokens"] == 90000

    response = await api_client.post(
        f"/analysis/{run_id}/retry", json={"allow_over_ceiling": True}, headers=_auth_header(token)
    )

    assert response.status_code == 202, response.text
    session_dep = app.dependency_overrides[get_db]
    async for session in session_dep():
        await bind_org(session, decode_access_token(token).org_id)
        retry = await session.get(AnalysisRun, uuid.UUID(response.json()["id"]))
        assert retry is not None and retry.config_snapshot["allow_over_ceiling"] is True
        await session.commit()
        break
    # A plain retry (no body) still works and doesn't override.
    await _fail(token, response.json()["id"])
    plain = await api_client.post(
        f"/analysis/{response.json()['id']}/retry", headers=_auth_header(token)
    )
    assert plain.status_code == 202
