import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import rustworkx as rx
from db.branch_index import BranchIndex, BranchIndexStatus
from db.organization import Organization
from db.provider import AIProvider
from db.pull_request import (
    AnalysisRun,
    AnalysisRunStatus,
    FindingRecord,
    PullRequest,
    PullRequestState,
)
from db.repository import Repository
from db.tenancy import bind_org
from revu.models import EvidenceItem, Finding, FindingCategory, RunResult, Severity
from revu.providers.llm import ModelEndpoint
from revu.verify import VerificationReport, VerificationResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from worker.jobs import analyze
from worker.storage import TenantStorage
from worker.testing import TEST_ACCESS, review_provider


async def _make_repo(session: AsyncSession, *, name: str = "acme/widgets") -> Repository:
    org = Organization(name=f"Org for {name}")
    session.add(org)
    await session.flush()
    # Act for this organisation, as a job does for the one in its
    # arguments: the session runs under row-level security.
    await bind_org(session, org.id)
    repo = Repository(org_id=org.id, full_name=name)
    session.add(repo)
    await session.flush()
    return repo


async def _make_run(
    session: AsyncSession,
    *,
    repo: Repository | None = None,
    model: str = "fake-model",
    agent: str = "diff_only",
    branch_index_id: uuid.UUID | None = None,
    supports_tools: bool | None = None,
) -> tuple[AnalysisRun, Repository]:
    repo = repo or await _make_repo(session)
    await bind_org(session, repo.org_id)
    provider = await review_provider(
        session, repo.org_id, model=model, supports_tools=supports_tools
    )
    pr = PullRequest(
        repo_id=repo.id,
        number=1,
        title="Fix bug",
        body="Fixes the loop bound.",
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
        config_snapshot={"agent": agent, **provider},
        status=AnalysisRunStatus.QUEUED,
        diff_text="diff text",
    )
    session.add(run)
    await session.flush()
    return run, repo


async def _make_ready_branch_index(
    session: AsyncSession, *, repo: Repository, graph_ref: str
) -> BranchIndex:
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


def _graph_ref_in(storage: TenantStorage, repo: Repository) -> str:
    return str(storage.index_dir(repo.org_id, repo.id) / "abc.graph.pkl.gz")


async def test_analyze_pr_persists_findings_and_marks_succeeded(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _repo = await _make_run(worker_db_session)

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

    await analyze.analyze_pr(worker_ctx, str(run.id), str(run.org_id))

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.SUCCEEDED
    assert run.tokens_in == 42
    assert run.tokens_out == 17
    assert run.finished_at is not None

    findings = (
        await worker_db_session.scalars(select(FindingRecord).where(FindingRecord.run_id == run.id))
    ).all()
    assert len(findings) == 1
    assert findings[0].file_path == "a.py"
    assert findings[0].evidence_json == [
        {"file_path": "a.py", "line_start": 1, "line_end": 2, "reason": "diff"}
    ]


async def test_analyze_pr_reads_the_diff_and_pr_text_from_its_own_rows(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the run id crosses the queue; nothing else is taken on trust."""
    run, _repo = await _make_run(worker_db_session)
    captured: dict[str, object] = {}

    async def fake_review_diff(**kwargs: object) -> RunResult:
        captured.update(kwargs)
        return RunResult()

    monkeypatch.setattr(analyze, "review_diff", fake_review_diff)

    await analyze.analyze_pr(worker_ctx, str(run.id), str(run.org_id))

    assert captured["diff"] == "diff text"
    assert captured["pr_title"] == "Fix bug"
    assert captured["pr_body"] == "Fixes the loop bound."


async def test_analyze_pr_is_always_diff_only(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manual path has no checkout, so even a run row claiming
    `cross_file` is reviewed from the diff alone."""
    run, _repo = await _make_run(worker_db_session, agent="cross_file")
    called = False

    async def fake_review_diff(**kwargs: object) -> RunResult:
        nonlocal called
        called = True
        return RunResult()

    monkeypatch.setattr(analyze, "review_diff", fake_review_diff)

    await analyze.analyze_pr(worker_ctx, str(run.id), str(run.org_id))

    assert called is True


async def test_analyze_pr_marks_failed_on_exception(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _repo = await _make_run(worker_db_session)

    async def raising_review_diff(**kwargs: object) -> RunResult:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(analyze, "review_diff", raising_review_diff)

    await analyze.analyze_pr(worker_ctx, str(run.id), str(run.org_id))

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.FAILED
    assert run.error == "provider unavailable"
    assert run.finished_at is not None


async def test_analyze_pr_uses_model_from_config_snapshot(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _repo = await _make_run(worker_db_session, model="claude-haiku-4-5")
    captured: dict[str, object] = {}

    async def fake_review_diff(**kwargs: object) -> RunResult:
        captured.update(kwargs)
        return RunResult()

    monkeypatch.setattr(analyze, "review_diff", fake_review_diff)

    await analyze.analyze_pr(worker_ctx, str(run.id), str(run.org_id))

    endpoint = captured["model"]
    assert isinstance(endpoint, ModelEndpoint)
    # The run's own provider, resolved and decrypted by the worker; never
    # anything from the environment.
    assert endpoint.model == "openai/claude-haiku-4-5"
    assert endpoint.api_key == "sk-test-key"
    assert endpoint.api_base == "http://127.0.0.1:11434/v1"


async def test_a_model_without_tool_calling_gets_a_diff_only_review_instead(
    worker_db_session: AsyncSession, tenant_storage: TenantStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, repo = await _make_run(worker_db_session, agent="cross_file", supports_tools=False)
    called: list[str] = []

    async def fake_review_diff(**kwargs: object) -> RunResult:
        called.append("diff_only")
        return RunResult()

    monkeypatch.setattr(analyze, "review_diff", fake_review_diff)
    monkeypatch.setattr(analyze, "review_cross_file", pytest.fail)

    await analyze.execute_review(
        worker_db_session,
        run,
        repo=repo,
        pr_title="t",
        pr_body="b",
        diff="d",
        agent="cross_file",
        repo_path="/tmp/acme-widgets",
        storage=tenant_storage,
        provider_access=TEST_ACCESS,
    )

    await worker_db_session.refresh(run)
    assert called == ["diff_only"]
    assert run.status == AnalysisRunStatus.SUCCEEDED
    assert "can't call tools" in run.config_snapshot["agent_fallback"]


async def test_a_run_whose_provider_was_deleted_fails_with_a_clear_reason(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object]
) -> None:
    run, _repo = await _make_run(worker_db_session)
    provider = await worker_db_session.get(
        AIProvider, uuid.UUID(run.config_snapshot["provider_id"])
    )
    await worker_db_session.delete(provider)
    await worker_db_session.flush()

    await analyze.analyze_pr(worker_ctx, str(run.id), str(run.org_id))

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.FAILED
    assert "has been deleted" in (run.error or "")


async def test_a_run_without_a_recorded_provider_never_falls_back_to_the_environment(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _repo = await _make_run(worker_db_session)
    run.config_snapshot = {"agent": "diff_only", "model": "claude-sonnet-5"}
    await worker_db_session.flush()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-the-environment")
    monkeypatch.setattr(analyze, "review_diff", pytest.fail)

    await analyze.analyze_pr(worker_ctx, str(run.id), str(run.org_id))

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.FAILED
    assert "no AI provider recorded" in (run.error or "")


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
    await analyze.analyze_pr(worker_ctx, str(uuid.uuid4()), str(uuid.uuid4()))

    assert called is False


async def test_cross_file_review_loads_graph_and_runs_through_verifier(
    worker_db_session: AsyncSession, tenant_storage: TenantStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `agent="cross_file"` path must: load the graph off the run's own
    `branch_index_id`, call `review_cross_file` with it, then run the raw
    findings through Stage 8's `verify_findings` before persisting — a
    tool-calling agent's self-cited evidence is exactly what that verifier
    exists to catch.
    """
    repo = await _make_repo(worker_db_session)
    branch_index = await _make_ready_branch_index(
        worker_db_session, repo=repo, graph_ref=_graph_ref_in(tenant_storage, repo)
    )
    run, _ = await _make_run(
        worker_db_session, repo=repo, agent="cross_file", branch_index_id=branch_index.id
    )

    raw_finding = Finding(
        file_path="a.py",
        line_start=1,
        line_end=2,
        category=FindingCategory.CORRECTNESS,
        severity=Severity.HIGH,
        message="fabricated citation",
        evidence=[EvidenceItem(file_path="a.py", line_start=1, line_end=2, reason="tool call")],
        confidence=0.9,
        agent_name="cross_file",
    )
    cross_file_result = RunResult(
        findings=[raw_finding],
        tokens_in=300,
        tokens_out=100,
        cost_usd=0.01,
        latency_ms=456,
    )
    verified_finding = raw_finding.model_copy(update={"confidence": 0.6})
    verification_result = VerificationResult(
        findings=[verified_finding],
        report=VerificationReport(
            findings_in=1,
            findings_after_dedup=1,
            merged_count=0,
            evidence_drop_rate=0.0,
            evidence_dropped_count=0,
            dropped_below_threshold=0,
            cut_by_cap=0,
            findings_out=1,
        ),
    )

    captured_review_kwargs: dict[str, object] = {}
    captured_verify_kwargs: dict[str, object] = {}
    captured_load: dict[str, object] = {}
    fake_graph = rx.PyDiGraph()

    def fake_load_graph(path: Path, *, signing_key: bytes) -> rx.PyDiGraph:
        captured_load.update(path=path, signing_key=signing_key)
        return fake_graph

    async def fake_review_cross_file(**kwargs: object) -> RunResult:
        captured_review_kwargs.update(kwargs)
        return cross_file_result

    def fake_verify_findings(findings: list[Finding], **kwargs: object) -> VerificationResult:
        captured_verify_kwargs["findings"] = findings
        captured_verify_kwargs.update(kwargs)
        return verification_result

    monkeypatch.setattr(analyze, "load_graph", fake_load_graph)
    monkeypatch.setattr(analyze, "review_cross_file", fake_review_cross_file)
    monkeypatch.setattr(analyze, "verify_findings", fake_verify_findings)

    await analyze.execute_review(
        worker_db_session,
        run,
        repo=repo,
        pr_title="t",
        pr_body="b",
        diff="d",
        agent="cross_file",
        repo_path="/tmp/acme-widgets",
        storage=tenant_storage,
        provider_access=TEST_ACCESS,
    )

    assert captured_load["signing_key"] == tenant_storage.signing_key
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
        await worker_db_session.scalars(select(FindingRecord).where(FindingRecord.run_id == run.id))
    ).all()
    assert len(findings) == 1
    # confidence is a Numeric column (Decimal); the verifier's score, not the raw one.
    assert float(findings[0].confidence) == pytest.approx(0.6)


async def test_cross_file_review_refuses_another_repositorys_index(
    worker_db_session: AsyncSession, tenant_storage: TenantStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a run pointing at a branch index from another tenant's
    repository must fail, never review against that tenant's code graph."""
    other_repo = await _make_repo(worker_db_session, name="other/secret")
    foreign_index = await _make_ready_branch_index(
        worker_db_session, repo=other_repo, graph_ref=_graph_ref_in(tenant_storage, other_repo)
    )
    run, repo = await _make_run(
        worker_db_session, agent="cross_file", branch_index_id=foreign_index.id
    )
    monkeypatch.setattr(analyze, "load_graph", pytest.fail)

    await analyze.execute_review(
        worker_db_session,
        run,
        repo=repo,
        pr_title="t",
        pr_body="b",
        diff="d",
        agent="cross_file",
        repo_path="/tmp/acme-widgets",
        storage=tenant_storage,
        provider_access=TEST_ACCESS,
    )

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.FAILED
    assert "different repository" in (run.error or "")


async def test_cross_file_review_refuses_a_graph_outside_the_repos_index_dir(
    worker_db_session: AsyncSession, tenant_storage: TenantStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _make_repo(worker_db_session)
    stray = await _make_ready_branch_index(
        worker_db_session, repo=repo, graph_ref="/data/index/other-org/x.graph.pkl.gz"
    )
    run, _ = await _make_run(
        worker_db_session, repo=repo, agent="cross_file", branch_index_id=stray.id
    )
    monkeypatch.setattr(analyze, "load_graph", pytest.fail)

    await analyze.execute_review(
        worker_db_session,
        run,
        repo=repo,
        pr_title="t",
        pr_body="b",
        diff="d",
        agent="cross_file",
        repo_path="/tmp/acme-widgets",
        storage=tenant_storage,
        provider_access=TEST_ACCESS,
    )

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.FAILED
    assert "outside the index directory" in (run.error or "")


async def test_cross_file_review_without_a_checkout_fails(
    worker_db_session: AsyncSession, tenant_storage: TenantStorage
) -> None:
    run, repo = await _make_run(worker_db_session, agent="cross_file")

    await analyze.execute_review(
        worker_db_session,
        run,
        repo=repo,
        pr_title="t",
        pr_body="b",
        diff="d",
        agent="cross_file",
        repo_path=None,
        storage=tenant_storage,
        provider_access=TEST_ACCESS,
    )

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.FAILED
    assert "checkout" in (run.error or "")


async def test_cross_file_review_without_branch_index_id_fails(
    worker_db_session: AsyncSession, tenant_storage: TenantStorage
) -> None:
    run, repo = await _make_run(worker_db_session, agent="cross_file", branch_index_id=None)

    await analyze.execute_review(
        worker_db_session,
        run,
        repo=repo,
        pr_title="t",
        pr_body="b",
        diff="d",
        agent="cross_file",
        repo_path="/tmp/acme-widgets",
        storage=tenant_storage,
        provider_access=TEST_ACCESS,
    )

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.FAILED
    assert "branch_index_id" in (run.error or "")
