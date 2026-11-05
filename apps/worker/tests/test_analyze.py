import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import litellm
import pytest
import rustworkx as rx
from arq import Retry
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
from observability import metrics
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


async def test_a_model_timeout_is_recorded_in_words_not_as_a_stack_of_library_text(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _repo = await _make_run(worker_db_session)

    async def slow_review_diff(**kwargs: object) -> RunResult:
        raise litellm.Timeout(message="timeout value=600.0", model="m", llm_provider="openai")

    monkeypatch.setattr(analyze, "review_diff", slow_review_diff)

    await analyze.analyze_pr(worker_ctx, str(run.id), str(run.org_id))

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.FAILED
    assert run.error is not None and run.error.startswith("The model didn't answer in time.")


async def test_how_the_review_went_is_kept_on_the_run(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _repo = await _make_run(worker_db_session)

    async def fake_review_diff(**kwargs: object) -> RunResult:
        return RunResult(
            skipped_files=["yarn.lock"], parts=2, stopped_reason="parts_failed (1 of 2)"
        )

    monkeypatch.setattr(analyze, "review_diff", fake_review_diff)

    await analyze.analyze_pr(worker_ctx, str(run.id), str(run.org_id))

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.SUCCEEDED
    assert run.config_snapshot["skipped_files"] == ["yarn.lock"]
    assert run.config_snapshot["parts"] == 2
    assert run.config_snapshot["stopped_reason"] == "parts_failed (1 of 2)"
    assert run.config_snapshot["agent"] == "diff_only"  # added to, not replaced


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


@pytest.mark.parametrize(
    ("recorded", "expected"),
    [("off", "off"), ("high", "high"), (None, None), ("bogus", None)],
)
async def test_the_runs_thinking_setting_reaches_the_model_call(
    worker_db_session: AsyncSession,
    worker_ctx: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    recorded: str | None,
    expected: str | None,
) -> None:
    """Runs queued before the setting existed (no key) or with an unknown
    value fall back to the model's own default instead of failing."""
    run, _repo = await _make_run(worker_db_session)
    run.config_snapshot = {**run.config_snapshot, "reasoning": recorded}
    await worker_db_session.flush()
    captured: dict[str, object] = {}

    async def fake_review_diff(**kwargs: object) -> RunResult:
        captured.update(kwargs)
        return RunResult()

    monkeypatch.setattr(analyze, "review_diff", fake_review_diff)

    await analyze.analyze_pr(worker_ctx, str(run.id), str(run.org_id))

    endpoint = captured["model"]
    assert isinstance(endpoint, ModelEndpoint)
    assert endpoint.reasoning == expected


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
    # The blast radius is read off the same graph (empty here, so nothing).
    assert run.blast_radius is not None and run.blast_radius["changed"] == 0

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


def _rate_limited() -> Exception:
    return litellm.RateLimitError(message="429", model="m", llm_provider="groq")


async def test_a_rate_limited_review_goes_back_in_the_queue_to_retry(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _repo = await _make_run(worker_db_session)

    async def busy_provider(**kwargs: object) -> RunResult:
        raise _rate_limited()

    monkeypatch.setattr(analyze, "review_diff", busy_provider)

    with pytest.raises(Retry) as retry:
        await analyze.analyze_pr({**worker_ctx, "job_try": 1}, str(run.id), str(run.org_id))

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.QUEUED
    assert run.config_snapshot["retries"] == 1
    assert retry.value.defer_score == analyze.RETRY_BASE_S * 1000  # arq keeps milliseconds


async def test_the_last_attempt_fails_the_run_instead_of_retrying(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _repo = await _make_run(worker_db_session)

    async def busy_provider(**kwargs: object) -> RunResult:
        raise _rate_limited()

    monkeypatch.setattr(analyze, "review_diff", busy_provider)

    ctx = {**worker_ctx, "job_try": analyze.REVIEW_MAX_TRIES}
    await analyze.analyze_pr(ctx, str(run.id), str(run.org_id))

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.FAILED
    assert "rate limiting" in (run.error or "")


async def test_a_timeout_is_not_retried(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model too slow for the request would only be too slow again."""
    run, _repo = await _make_run(worker_db_session)

    async def slow(**kwargs: object) -> RunResult:
        raise litellm.Timeout(message="timeout", model="m", llm_provider="openai")

    monkeypatch.setattr(analyze, "review_diff", slow)

    await analyze.analyze_pr({**worker_ctx, "job_try": 1}, str(run.id), str(run.org_id))

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.FAILED


async def test_an_interrupted_review_is_marked_failed_not_left_running(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _repo = await _make_run(worker_db_session)

    async def cut_off(**kwargs: object) -> RunResult:
        raise asyncio.CancelledError  # what arq's job time limit does

    monkeypatch.setattr(analyze, "review_diff", cut_off)

    with pytest.raises(asyncio.CancelledError):
        await analyze.analyze_pr(worker_ctx, str(run.id), str(run.org_id))

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.FAILED
    assert run.error == analyze.INTERRUPTED


async def test_runs_a_crashed_worker_left_behind_are_failed(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object]
) -> None:
    now = datetime.now(UTC)
    abandoned, _ = await _make_run(worker_db_session)
    abandoned.status = AnalysisRunStatus.RUNNING
    abandoned.started_at = now - timedelta(seconds=analyze.REVIEW_JOB_TIMEOUT_S + 3600)
    working, _ = await _make_run(worker_db_session)
    working.status = AnalysisRunStatus.RUNNING
    working.started_at = now - timedelta(minutes=5)
    forgotten, _ = await _make_run(worker_db_session)
    forgotten.created_at = now - timedelta(days=2)
    waiting, _ = await _make_run(worker_db_session)
    await worker_db_session.flush()

    marked = await analyze.fail_stuck_runs(worker_ctx)

    for run in (abandoned, working, forgotten, waiting):
        await bind_org(worker_db_session, run.org_id)  # each is another organisation's
        await worker_db_session.refresh(run)
    assert marked >= 2
    assert abandoned.status == forgotten.status == AnalysisRunStatus.FAILED
    assert abandoned.error == analyze.INTERRUPTED
    assert working.status == AnalysisRunStatus.RUNNING
    assert waiting.status == AnalysisRunStatus.QUEUED


def _sample(name: str, **labels: str) -> float:
    value = metrics.REGISTRY.get_sample_value(name, labels)
    return value or 0.0


async def test_review_outcomes_tokens_and_cost_reach_the_metrics(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _repo = await _make_run(worker_db_session)
    finding = Finding(
        file_path="a.py", line_start=1, line_end=1, category=FindingCategory.CORRECTNESS,
        severity=Severity.CRITICAL, message="bug", evidence=[], confidence=0.9,
        agent_name="diff_only",
    )

    async def review(**kwargs: object) -> RunResult:
        return RunResult(
            findings=[finding], tokens_in=100, tokens_out=40, cost_usd=0.5, latency_ms=2000
        )

    monkeypatch.setattr(analyze, "review_diff", review)
    before = {
        "ok": _sample("revu_reviews_total", agent="diff_only", outcome="succeeded"),
        "in": _sample("revu_review_tokens_total", direction="in"),
        "cost": _sample("revu_review_cost_usd_total"),
        "critical": _sample("revu_review_findings_total", severity="critical"),
    }

    await analyze.analyze_pr(worker_ctx, str(run.id), str(run.org_id))

    assert _sample("revu_reviews_total", agent="diff_only", outcome="succeeded") == before["ok"] + 1
    assert _sample("revu_review_tokens_total", direction="in") == before["in"] + 100
    assert _sample("revu_review_cost_usd_total") == pytest.approx(before["cost"] + 0.5)
    assert (
        _sample("revu_review_findings_total", severity="critical") == before["critical"] + 1
    )


async def test_a_failed_and_a_retried_review_are_counted_apart(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def busy_provider(**kwargs: object) -> RunResult:
        raise _rate_limited()

    monkeypatch.setattr(analyze, "review_diff", busy_provider)
    retried = _sample("revu_reviews_total", agent="diff_only", outcome="retried")
    failed = _sample("revu_reviews_total", agent="diff_only", outcome="failed")

    first, _ = await _make_run(worker_db_session)
    with pytest.raises(Retry):
        await analyze.analyze_pr({**worker_ctx, "job_try": 1}, str(first.id), str(first.org_id))
    other = await _make_repo(worker_db_session, name="acme/other")
    last, _ = await _make_run(worker_db_session, repo=other)
    ctx = {**worker_ctx, "job_try": analyze.REVIEW_MAX_TRIES}
    await analyze.analyze_pr(ctx, str(last.id), str(last.org_id))

    assert _sample("revu_reviews_total", agent="diff_only", outcome="retried") == retried + 1
    assert _sample("revu_reviews_total", agent="diff_only", outcome="failed") == failed + 1


async def test_a_finished_review_is_added_to_the_usage_ledger_once(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    from db.ledger import TokenUsageLedger

    run, _repo = await _make_run(worker_db_session)

    async def fake_review_diff(**kwargs: object) -> RunResult:
        return RunResult(findings=[], tokens_in=300, tokens_out=50, cost_usd=0.25, latency_ms=5)

    monkeypatch.setattr(analyze, "review_diff", fake_review_diff)
    await analyze.analyze_pr(worker_ctx, str(run.id), str(run.org_id))

    rows = (
        await worker_db_session.scalars(
            select(TokenUsageLedger).where(TokenUsageLedger.run_id == run.id)
        )
    ).all()
    assert [(r.tokens_in, r.tokens_out, float(r.cost_usd)) for r in rows] == [(300, 50, 0.25)]


async def test_a_queued_review_does_not_start_once_the_budget_is_reached(
    worker_db_session: AsyncSession, worker_ctx: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    from decimal import Decimal

    from db.ledger import TokenUsageLedger

    earlier, repo = await _make_run(worker_db_session)
    earlier.status = AnalysisRunStatus.SUCCEEDED
    worker_db_session.add(
        TokenUsageLedger(
            org_id=repo.org_id, run_id=earlier.id, tokens_in=10, tokens_out=0, cost_usd=5
        )
    )
    org = await worker_db_session.get(Organization, repo.org_id)
    assert org is not None
    org.monthly_budget_usd = Decimal("5.00")
    run = AnalysisRun(
        pr_id=earlier.pr_id,
        config_snapshot=earlier.config_snapshot,
        status=AnalysisRunStatus.QUEUED,
        diff_text="diff text",
    )
    worker_db_session.add(run)
    await worker_db_session.commit()
    called = False

    async def fake_review_diff(**kwargs: object) -> RunResult:
        nonlocal called
        called = True
        return RunResult(findings=[])

    monkeypatch.setattr(analyze, "review_diff", fake_review_diff)
    await analyze.analyze_pr(worker_ctx, str(run.id), str(run.org_id))

    await worker_db_session.refresh(run)
    assert not called
    assert run.status == AnalysisRunStatus.FAILED
    assert "monthly budget of $5.00" in (run.error or "")


async def test_a_blast_radius_failure_does_not_fail_the_review(
    worker_db_session: AsyncSession, tenant_storage: TenantStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _make_repo(worker_db_session)
    branch_index = await _make_ready_branch_index(
        worker_db_session, repo=repo, graph_ref=_graph_ref_in(tenant_storage, repo)
    )
    run, _ = await _make_run(
        worker_db_session, repo=repo, agent="cross_file", branch_index_id=branch_index.id
    )

    def broken(*args: object, **kwargs: object) -> None:
        raise ValueError("unexpected graph shape")

    async def fake_review_cross_file(**kwargs: object) -> RunResult:
        return RunResult(findings=[])

    monkeypatch.setattr(analyze, "load_graph", lambda *a, **k: rx.PyDiGraph())
    monkeypatch.setattr(analyze, "blast_radius", broken)
    monkeypatch.setattr(analyze, "review_cross_file", fake_review_cross_file)

    await analyze.execute_review(
        worker_db_session, run, repo=repo, pr_title="t", pr_body="b", diff="d",
        agent="cross_file", repo_path="/tmp/acme-widgets", storage=tenant_storage,
        provider_access=TEST_ACCESS,
    )

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.SUCCEEDED
    assert run.blast_radius is None
