import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import rustworkx as rx
from db.branch_index import BranchIndex, BranchIndexStatus
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
from revu.verify import VerificationReport, VerificationResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from worker.jobs import analyze


async def _make_run(
    session: AsyncSession,
    *,
    model: str = "fake-model",
    agent: str = "diff_only",
    branch_index_id: uuid.UUID | None = None,
) -> AnalysisRun:
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
        branch_index_id=branch_index_id,
        config_snapshot={"agent": agent, "model": model},
        status=AnalysisRunStatus.QUEUED,
    )
    session.add(run)
    await session.flush()
    return run


async def _make_ready_branch_index(session: AsyncSession, *, graph_ref: str) -> BranchIndex:
    org = Organization(name="Other Org")
    session.add(org)
    await session.flush()

    repo = Repository(org_id=org.id, full_name="acme/other-widgets")
    session.add(repo)
    await session.flush()

    branch_index = BranchIndex(
        repo_id=repo.id,
        branch_name="main",
        head_sha="a" * 40,
        status=BranchIndexStatus.READY,
        node_count=1,
        edge_count=0,
        graph_ref=graph_ref,
        build_duration_ms=10,
        created_at=datetime.now(UTC),
    )
    session.add(branch_index)
    await session.flush()
    return branch_index


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


async def test_analyze_pr_cross_file_loads_graph_and_runs_through_verifier(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `agent="cross_file"` path must: load the graph off the run's own
    `branch_index_id` (set by `api.services.analysis.create_analysis_run`),
    call `review_cross_file` with it, then run the raw findings through
    Stage 8's `verify_findings` before persisting — a tool-calling agent's
    self-cited evidence is exactly what that verifier exists to catch.
    """
    branch_index = await _make_ready_branch_index(
        worker_db_session, graph_ref="/blobs/does-not-need-to-exist.graph.pkl.gz"
    )
    run = await _make_run(
        worker_db_session, agent="cross_file", branch_index_id=branch_index.id
    )

    raw_finding = Finding(
        file_path="a.py", line_start=1, line_end=2, category=FindingCategory.CORRECTNESS,
        severity=Severity.HIGH, message="fabricated citation",
        evidence=[EvidenceItem(file_path="a.py", line_start=1, line_end=2, reason="tool call")],
        confidence=0.9, agent_name="cross_file",
    )
    cross_file_result = RunResult(
        findings=[raw_finding], tokens_in=300, tokens_out=100, cost_usd=0.01, latency_ms=456,
    )
    verified_finding = raw_finding.model_copy(update={"confidence": 0.6})
    verification_result = VerificationResult(
        findings=[verified_finding],
        report=VerificationReport(
            findings_in=1, findings_after_dedup=1, merged_count=0, evidence_drop_rate=0.0,
            evidence_dropped_count=0, dropped_below_threshold=0, cut_by_cap=0, findings_out=1,
        ),
    )

    captured_review_kwargs: dict[str, object] = {}
    captured_verify_kwargs: dict[str, object] = {}
    fake_graph = rx.PyDiGraph()

    async def fake_review_cross_file(**kwargs: object) -> RunResult:
        captured_review_kwargs.update(kwargs)
        return cross_file_result

    def fake_verify_findings(findings: list[Finding], **kwargs: object) -> VerificationResult:
        captured_verify_kwargs["findings"] = findings
        captured_verify_kwargs.update(kwargs)
        return verification_result

    monkeypatch.setattr(analyze, "load_graph", lambda path: fake_graph)
    monkeypatch.setattr(analyze, "review_cross_file", fake_review_cross_file)
    monkeypatch.setattr(analyze, "verify_findings", fake_verify_findings)

    await analyze.analyze_pr(
        worker_ctx, str(run.id), "t", "b", "d", "cross_file", "/tmp/acme-widgets"
    )

    assert captured_review_kwargs["graph"] is fake_graph
    assert captured_review_kwargs["repo_root"] == Path("/tmp/acme-widgets")
    assert captured_verify_kwargs["findings"] == [raw_finding]
    assert captured_verify_kwargs["repo_root"] == Path("/tmp/acme-widgets")

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.SUCCEEDED
    assert run.tokens_in == 300
    assert run.tokens_out == 100
    assert run.config_snapshot["verification"]["findings_out"] == 1

    findings = (
        await worker_db_session.scalars(
            select(FindingRecord).where(FindingRecord.run_id == run.id)
        )
    ).all()
    assert len(findings) == 1
    # confidence is a Numeric column (Decimal); the verifier's score, not the raw one.
    assert float(findings[0].confidence) == pytest.approx(0.6)


async def test_analyze_pr_cross_file_without_repo_path_fails(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object]
) -> None:
    run = await _make_run(worker_db_session, agent="cross_file")

    await analyze.analyze_pr(worker_ctx, str(run.id), "t", "b", "d", "cross_file", None)

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.FAILED
    assert "repo_path" in (run.error or "")


async def test_analyze_pr_cross_file_without_branch_index_id_fails(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object]
) -> None:
    run = await _make_run(worker_db_session, agent="cross_file", branch_index_id=None)

    await analyze.analyze_pr(
        worker_ctx, str(run.id), "t", "b", "d", "cross_file", "/tmp/acme-widgets"
    )

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.FAILED
    assert "branch_index_id" in (run.error or "")
