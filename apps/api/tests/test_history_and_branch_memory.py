"""GET /history (+ /history/stats) and GET /branch-memory."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from api.core.security import decode_access_token
from api.db.session import get_db
from api.main import app
from api.testing import configure_review_model
from db.branch_index import BranchIndex, BranchIndexStatus, IndexUpdateLog, IndexUpdateMode
from db.pull_request import (
    AnalysisRun,
    AnalysisRunStatus,
    DeveloperAction,
    FindingRecord,
    PullRequest,
    PullRequestState,
)
from db.repository import Repository
from db.tenancy import bind_org
from httpx import AsyncClient
from revu.models import FindingCategory, Severity
from sqlalchemy.ext.asyncio import AsyncSession


async def _signup(client: AsyncClient, email: str) -> dict[str, str]:
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
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    await configure_review_model(client, headers)
    return headers


def _org(headers: dict[str, str]) -> uuid.UUID:
    return decode_access_token(headers["Authorization"].removeprefix("Bearer ")).org_id


async def _session(headers: dict[str, str]) -> AsyncIterator[AsyncSession]:
    async for session in app.dependency_overrides[get_db]():
        await bind_org(session, _org(headers))
        yield session
        break


async def _review(
    client: AsyncClient, headers: dict[str, str], *, repo: str = "acme/widgets", pr: int = 1
) -> str:
    created = await client.post(
        "/analysis",
        json={
            "repo_full_name": repo,
            "base_branch": "main",
            "head_sha": "abc1234",
            "pr_number": pr,
            "pr_title": f"PR {pr}",
            "pr_body": "",
            "diff": "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n",
        },
        headers=headers,
    )
    assert created.status_code == 202, created.text
    return str(created.json()["id"])


async def _finish(
    headers: dict[str, str],
    run_id: str,
    *,
    severities: list[Severity] = (),  # type: ignore[assignment]
    actions: list[DeveloperAction | None] | None = None,
    status: AnalysisRunStatus = AnalysisRunStatus.SUCCEEDED,
    merged: bool = False,
) -> None:
    async for session in _session(headers):
        run = await session.get(AnalysisRun, uuid.UUID(run_id))
        assert run is not None
        run.status = status
        run.finished_at = datetime.now(UTC)
        for i, severity in enumerate(severities):
            session.add(
                FindingRecord(
                    run_id=run.id,
                    org_id=run.org_id,
                    file_path="x.py",
                    line_start=1,
                    line_end=1,
                    category=FindingCategory.CORRECTNESS,
                    severity=severity,
                    message="m",
                    confidence=0.9,
                    agent_name="diff_only",
                    developer_action=actions[i] if actions else None,
                )
            )
        if merged:
            pr = await session.get(PullRequest, run.pr_id)
            assert pr is not None
            pr.state = PullRequestState.MERGED
        await session.commit()


async def test_history_lists_runs_newest_first_with_their_findings(
    api_client: AsyncClient,
) -> None:
    headers = await _signup(api_client, "history@example.com")
    first = await _review(api_client, headers, pr=1)
    await _finish(headers, first, severities=[Severity.HIGH, Severity.HIGH, Severity.LOW])
    second = await _review(api_client, headers, pr=2)

    page = (await api_client.get("/history", headers=headers)).json()

    assert [r["id"] for r in page["runs"]] == [second, first]
    older = page["runs"][1]
    assert older["findings"] == 3 and older["severities"] == {"high": 2, "low": 1}
    assert older["repo_full_name"] == "acme/widgets" and older["pr_number"] == 1
    assert older["status"] == "succeeded" and older["source"] == "manual"
    assert page["next_before"] is None


async def test_history_filters_and_pages(api_client: AsyncClient) -> None:
    headers = await _signup(api_client, "filters@example.com")
    critical = await _review(api_client, headers, pr=1)
    await _finish(headers, critical, severities=[Severity.CRITICAL], merged=True)
    low = await _review(api_client, headers, pr=2)
    await _finish(headers, low, severities=[Severity.LOW])
    failed = await _review(api_client, headers, repo="acme/other", pr=3)
    await _finish(headers, failed, status=AnalysisRunStatus.FAILED)

    async def ids(query: str) -> list[str]:
        response = await api_client.get(f"/history?{query}", headers=headers)
        assert response.status_code == 200, response.text
        return [r["id"] for r in response.json()["runs"]]

    assert await ids("severity=high") == [critical]  # high or above
    assert await ids("severity=low") == [low, critical]
    assert await ids("outcome=failed") == [failed]
    assert await ids("pr_state=merged") == [critical]
    other_repo = (await api_client.get("/history", headers=headers)).json()["runs"][0]["repo_id"]
    assert await ids(f"repo_id={other_repo}") == [failed]

    page1 = (await api_client.get("/history?limit=2", headers=headers)).json()
    assert len(page1["runs"]) == 2 and page1["next_before"]
    page2 = (
        await api_client.get(
            "/history", params={"limit": 2, "before": page1["next_before"]}, headers=headers
        )
    ).json()
    assert [r["id"] for r in page2["runs"]] == [critical]
    assert page2["next_before"] is None


async def test_adoption_stats_count_outcomes_and_developer_feedback(
    api_client: AsyncClient,
) -> None:
    headers = await _signup(api_client, "stats@example.com")
    empty = (await api_client.get("/history/stats", headers=headers)).json()
    assert empty["runs"] == 0 and empty["acceptance_rate"] is None

    run = await _review(api_client, headers, pr=1)
    await _finish(
        headers,
        run,
        severities=[Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.LOW],
        actions=[
            DeveloperAction.ACCEPTED,
            DeveloperAction.ACCEPTED,
            DeveloperAction.DISMISSED,
            None,
        ],
        merged=True,
    )
    failed = await _review(api_client, headers, pr=2)
    await _finish(headers, failed, status=AnalysisRunStatus.FAILED)
    await _review(api_client, headers, pr=3)

    stats = (await api_client.get("/history/stats", headers=headers)).json()

    assert (stats["runs"], stats["succeeded"], stats["failed"]) == (3, 1, 1)
    assert stats["in_progress"] == 1
    assert (stats["pull_requests"], stats["merged_pull_requests"]) == (3, 1)
    assert stats["findings"] == 4
    assert stats["by_severity"] == {"high": 1, "medium": 1, "low": 2}
    assert (stats["accepted"], stats["dismissed"], stats["no_feedback"]) == (2, 1, 1)
    assert stats["acceptance_rate"] == 0.667


async def test_history_is_per_organization(api_client: AsyncClient) -> None:
    mine = await _signup(api_client, "mine@example.com")
    await _review(api_client, mine)
    theirs = await _signup(api_client, "theirs@example.com")

    assert (await api_client.get("/history", headers=theirs)).json()["runs"] == []
    assert (await api_client.get("/history/stats", headers=theirs)).json()["runs"] == 0


async def _github_repo(session: AsyncSession, org_id: uuid.UUID, name: str) -> Repository:
    repo = Repository(
        org_id=org_id, full_name=name, github_repo_id=abs(hash(name)) % 10**9, default_branch="main"
    )
    session.add(repo)
    await session.flush()
    return repo


def _attempt(
    repo: Repository, branch: str, status: BranchIndexStatus, *, at: datetime, **fields: object
) -> BranchIndex:
    return BranchIndex(
        repo_id=repo.id,
        org_id=repo.org_id,
        branch_name=branch,
        status=status,
        created_at=at,
        **fields,
    )


async def test_branch_memory_shows_each_branch_and_what_keeping_it_fresh_cost(
    api_client: AsyncClient,
) -> None:
    headers = await _signup(api_client, "memory@example.com")
    now = datetime.now(UTC)
    async for session in _session(headers):
        indexed = await _github_repo(session, _org(headers), "acme/indexed")
        await _github_repo(session, _org(headers), "acme/never")
        ready = _attempt(
            indexed,
            "main",
            BranchIndexStatus.READY,
            at=now - timedelta(hours=2),
            head_sha="a" * 40,
            node_count=120,
            edge_count=90,
            unresolved_symbols=[{"name": f"s{i}"} for i in range(10)],
            build_duration_ms=1500,
            built_at=now - timedelta(hours=2),
        )
        session.add(ready)
        # A newer attempt is building: the ready one stays in use.
        session.add(_attempt(indexed, "main", BranchIndexStatus.BUILDING, at=now))
        session.add(_attempt(indexed, "release", BranchIndexStatus.FAILED, at=now))
        await session.flush()
        for mode, age in [
            (IndexUpdateMode.FULL, timedelta(days=40)),  # outside the window
            (IndexUpdateMode.FULL, timedelta(days=3)),
            (IndexUpdateMode.INCREMENTAL, timedelta(days=1)),
            (IndexUpdateMode.INCREMENTAL, timedelta(hours=2)),
        ]:
            session.add(
                IndexUpdateLog(
                    branch_index_id=ready.id,
                    org_id=ready.org_id,
                    to_sha="a" * 40,
                    mode=mode,
                    duration_ms=800,
                    files_changed=2,
                    created_at=now - age,
                )
            )
        await session.commit()

    rows = (await api_client.get("/branch-memory", headers=headers)).json()

    by_key = {(r["repo_full_name"], r["branch_name"]): r for r in rows}
    assert list(by_key) == [
        ("acme/indexed", "main"),
        ("acme/indexed", "release"),
        ("acme/never", "main"),
    ]
    main = by_key[("acme/indexed", "main")]
    assert main["state"] == "building" and main["has_ready_index"]
    assert (main["node_count"], main["edge_count"], main["unresolved_count"]) == (120, 90, 10)
    assert main["unresolved_rate"] == 0.1  # 10 of 100 references
    assert (main["full_rebuilds_30d"], main["incremental_updates_30d"]) == (1, 2)
    assert main["last_update"]["mode"] == "incremental"
    assert main["is_default_branch"]
    release = by_key[("acme/indexed", "release")]
    assert release["state"] == "failed" and not release["has_ready_index"]
    assert release["unresolved_rate"] is None
    assert by_key[("acme/never", "main")]["state"] == "not_indexed"

    other = await _signup(api_client, "memory-other@example.com")
    assert (await api_client.get("/branch-memory", headers=other)).json() == []
