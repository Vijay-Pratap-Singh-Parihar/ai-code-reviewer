import uuid
from datetime import UTC, datetime

import pytest
from db.organization import Organization
from db.pull_request import (
    AnalysisRun,
    AnalysisRunStatus,
    FindingRecord,
    PullRequest,
    PullRequestState,
)
from db.repository import Repository
from revu.models import EvidenceItem, Finding, FindingCategory, RunResult, Severity
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from worker.jobs import analyze


async def _make_run(session: AsyncSession, *, model: str = "fake-model") -> AnalysisRun:
    org = Organization(name="Acme Inc")
    session.add(org)
    await session.flush()

    repo = Repository(org_id=org.id, full_name="acme/widgets")
    session.add(repo)
    await session.flush()

    pr = PullRequest(
        repo_id=repo.id,
        number=1,
        title="Fix bug",
        body="",
        author="dev@example.com",
        base_branch="main",
        head_sha="abc1234",
        state=PullRequestState.OPEN,
        opened_at=datetime.now(UTC),
    )
    session.add(pr)
    await session.flush()

    run = AnalysisRun(
        pr_id=pr.id,
        config_snapshot={"agent": "diff_only", "model": model},
        status=AnalysisRunStatus.QUEUED,
    )
    session.add(run)
    await session.flush()
    return run


async def test_analyze_pr_persists_findings_and_marks_succeeded(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    run = await _make_run(worker_db_session)

    fake_result = RunResult(
        findings=[
            Finding(
                file_path="a.py",
                line_start=1,
                line_end=2,
                category=FindingCategory.CORRECTNESS,
                severity=Severity.HIGH,
                message="bug",
                evidence=[EvidenceItem(file_path="a.py", line_start=1, line_end=2, reason="diff")],
                confidence=0.8,
                agent_name="diff_only",
            )
        ],
        tokens_in=42,
        tokens_out=17,
        cost_usd=0.002,
        latency_ms=123,
    )

    async def fake_review_diff(**kwargs: object) -> RunResult:
        return fake_result

    monkeypatch.setattr(analyze, "review_diff", fake_review_diff)

    await analyze.analyze_pr(worker_ctx, str(run.id), "Fix bug", "", "diff text")

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.SUCCEEDED
    assert run.tokens_in == 42
    assert run.tokens_out == 17
    assert run.finished_at is not None

    findings = (
        await worker_db_session.scalars(
            select(FindingRecord).where(FindingRecord.run_id == run.id)
        )
    ).all()
    assert len(findings) == 1
    assert findings[0].file_path == "a.py"
    assert findings[0].evidence_json == [
        {"file_path": "a.py", "line_start": 1, "line_end": 2, "reason": "diff"}
    ]


async def test_analyze_pr_marks_failed_on_exception(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    run = await _make_run(worker_db_session)

    async def raising_review_diff(**kwargs: object) -> RunResult:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(analyze, "review_diff", raising_review_diff)

    await analyze.analyze_pr(worker_ctx, str(run.id), "Fix bug", "", "diff text")

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.FAILED
    assert run.error == "provider unavailable"
    assert run.finished_at is not None


async def test_analyze_pr_uses_model_from_config_snapshot(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    run = await _make_run(worker_db_session, model="claude-haiku-4-5")
    captured: dict[str, object] = {}

    async def fake_review_diff(**kwargs: object) -> RunResult:
        captured.update(kwargs)
        return RunResult()

    monkeypatch.setattr(analyze, "review_diff", fake_review_diff)

    await analyze.analyze_pr(worker_ctx, str(run.id), "t", "b", "d")

    assert captured["model"] == "claude-haiku-4-5"


async def test_analyze_pr_returns_early_when_run_missing(
    worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    async def should_not_be_called(**kwargs: object) -> RunResult:
        nonlocal called
        called = True
        return RunResult()

    monkeypatch.setattr(analyze, "review_diff", should_not_be_called)

    # Must not raise even though the run doesn't exist.
    await analyze.analyze_pr(worker_ctx, str(uuid.uuid4()), "t", "b", "d")

    assert called is False
