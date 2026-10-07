import uuid

from api.core.security import decode_access_token
from api.db.session import get_db
from api.main import app
from db.pull_request import AnalysisRun, FindingRecord
from db.tenancy import bind_org
from httpx import AsyncClient
from revu.models import FindingCategory, Severity


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
    # Only the run id and its organisation cross the queue; the worker
    # reads everything else back from the rows this request wrote.
    assert args[0] == body["id"]
    assert len(args) == 2 and uuid.UUID(str(args[1]))


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


async def test_same_repo_name_in_two_orgs_gives_each_its_own_isolated_copy(
    api_client: AsyncClient,
) -> None:
    """Repositories are unique per organisation: a second org reviewing the
    same repository gets its own row and runs, and neither can read the
    other's."""
    token_a = await _signup_and_get_token(api_client, "copy-a@example.com")
    run_a = await api_client.post(
        "/analysis", json=_analysis_payload(), headers=_auth_header(token_a)
    )
    assert run_a.status_code == 202, run_a.text

    token_b = await _signup_and_get_token(api_client, "copy-b@example.com")
    run_b = await api_client.post(
        "/analysis", json=_analysis_payload(), headers=_auth_header(token_b)
    )
    assert run_b.status_code == 202, run_b.text

    repos_a = (await api_client.get("/repos", headers=_auth_header(token_a))).json()
    repos_b = (await api_client.get("/repos", headers=_auth_header(token_b))).json()
    assert [r["full_name"] for r in repos_a] == ["acme/widgets"]
    assert [r["full_name"] for r in repos_b] == ["acme/widgets"]
    assert repos_a[0]["id"] != repos_b[0]["id"]

    cross = await api_client.get(
        f"/analysis/{run_a.json()['id']}", headers=_auth_header(token_b)
    )
    assert cross.status_code == 404


async def test_trigger_analysis_rejects_cross_file(api_client: AsyncClient) -> None:
    """Cross-file review reads repository files, so it is only available for
    GitHub-connected repos, where the worker owns the checkout."""
    token = await _signup_and_get_token(api_client, "cross-file-manual@example.com")

    payload = _analysis_payload(agent="cross_file")
    response = await api_client.post("/analysis", json=payload, headers=_auth_header(token))

    assert response.status_code == 422
    assert api_client.fake_redis_pool.enqueued == []  # type: ignore[attr-defined]


async def test_trigger_analysis_rejects_a_worker_filesystem_path(api_client: AsyncClient) -> None:
    """Regression: `repo_path` used to let any user point the worker at any
    directory it could read, including another tenant's clone."""
    token = await _signup_and_get_token(api_client, "repo-path@example.com")

    payload = _analysis_payload(repo_path="/data/repos")
    response = await api_client.post("/analysis", json=payload, headers=_auth_header(token))

    assert response.status_code == 422
    assert api_client.fake_redis_pool.enqueued == []  # type: ignore[attr-defined]


async def test_get_analysis_includes_each_finding_s_evidence_trail(
    api_client: AsyncClient,
) -> None:
    """The PR analysis view's evidence trail reads `FindingPublic.evidence`,
    which maps onto `FindingRecord.evidence_json` (JSONB) via a
    `validation_alias` — this proves that mapping actually round-trips
    through a real Postgres row, not just that the Pydantic model compiles.
    """
    token = await _signup_and_get_token(api_client, "evidence-trail@example.com")
    create_response = await api_client.post(
        "/analysis", json=_analysis_payload(), headers=_auth_header(token)
    )
    run_id = create_response.json()["id"]

    session_dep = app.dependency_overrides[get_db]
    async for session in session_dep():
        await bind_org(session, decode_access_token(token).org_id)
        run = await session.get(AnalysisRun, uuid.UUID(run_id))
        assert run is not None
        session.add(
            FindingRecord(
                run_id=run.id,
                file_path="app.py",
                line_start=2,
                line_end=2,
                category=FindingCategory.CORRECTNESS,
                severity=Severity.HIGH,
                message="off-by-one",
                evidence_json=[
                    {
                        "file_path": "callers.py",
                        "line_start": 5,
                        "line_end": 7,
                        "reason": "resolved caller of the changed function",
                    }
                ],
                confidence=0.9,
                agent_name="diff_only",
            )
        )
        await session.commit()
        break

    response = await api_client.get(f"/analysis/{run_id}", headers=_auth_header(token))

    assert response.status_code == 200
    findings = response.json()["findings"]
    assert len(findings) == 1
    assert findings[0]["evidence"] == [
        {
            "file_path": "callers.py",
            "line_start": 5,
            "line_end": 7,
            "reason": "resolved caller of the changed function",
        }
    ]


async def test_get_analysis_returns_stored_diff_and_pr_context(api_client: AsyncClient) -> None:
    """Stage 10: the PR view renders from the server, not browser storage."""
    token = await _signup_and_get_token(api_client, "context@example.com")
    payload = _analysis_payload()
    created = await api_client.post("/analysis", json=payload, headers=_auth_header(token))

    run_id = created.json()["id"]
    response = await api_client.get(f"/analysis/{run_id}", headers=_auth_header(token))

    body = response.json()
    assert body["diff"] == payload["diff"]
    assert body["agent"] == "diff_only"
    assert body["repo_full_name"] == "acme/widgets"
    assert (body["pr_number"], body["pr_title"]) == (1, "Fix off-by-one")
    assert (body["base_branch"], body["head_sha"]) == ("main", "abc1234")
