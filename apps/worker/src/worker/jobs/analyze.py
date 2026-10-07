import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from db.branch_index import BranchIndex
from db.pull_request import AnalysisRun, AnalysisRunStatus, FindingRecord, PullRequest
from db.repository import Repository
from db.tenancy import bind_org
from revu.agents.cross_file import review_cross_file
from revu.agents.diff_only import review_diff
from revu.index.store import load_graph
from revu.models import Finding
from revu.verify import verify_findings
from sqlalchemy.ext.asyncio import AsyncSession

from worker.storage import TenantStorage

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-sonnet-5"


async def _run_cross_file(
    *, run: AnalysisRun, session: AsyncSession, repo: Repository, storage: TenantStorage,
    pr_title: str, pr_body: str, diff: str, repo_path: str, model: str,
) -> list[Finding]:
    """The `agent="cross_file"` path: load the graph the caller's already-`ready`
    branch index built (validated at request time by
    `api.services.analysis.create_analysis_run` — a missing/not-ready index is
    a 409 the caller sees immediately, not a job failure discovered later),
    then run every finding through Stage 8's verifier before persisting, since
    a tool-calling agent's self-cited evidence is exactly what that verifier
    was built to catch when fabricated (see IMPLEMENTATION_PLAN.md Stage 8's
    "wiring both cross_file and verify_findings into that job together").
    """
    if run.branch_index_id is None:
        raise RuntimeError("cross_file run has no branch_index_id set")

    branch_index = await session.get(BranchIndex, run.branch_index_id)
    if branch_index is None or branch_index.graph_ref is None:
        raise RuntimeError(f"branch index {run.branch_index_id} has no usable graph_ref")
    # The run row says which index to use; never trust that it belongs to
    # the run's own repository without checking.
    if branch_index.repo_id != repo.id:
        raise RuntimeError(f"branch index {branch_index.id} belongs to a different repository")

    graph_path = storage.require_index_path(Path(branch_index.graph_ref), repo.org_id, repo.id)
    graph = load_graph(graph_path, signing_key=storage.signing_key)
    repo_root = Path(repo_path)

    result = await review_cross_file(
        pr_title=pr_title, pr_body=pr_body, diff=diff,
        repo_root=repo_root, graph=graph, model=model,
    )

    verification = verify_findings(result.findings, repo_root=repo_root)
    run.config_snapshot = {**run.config_snapshot, "verification": asdict(verification.report)}
    run.tokens_in = result.tokens_in
    run.tokens_out = result.tokens_out
    run.cost_usd = result.cost_usd
    run.latency_ms = result.latency_ms
    return verification.findings


async def execute_review(
    session: AsyncSession,
    run: AnalysisRun,
    *,
    repo: Repository,
    pr_title: str,
    pr_body: str,
    diff: str,
    agent: str,
    repo_path: str | None,
    storage: TenantStorage | None,
) -> None:
    """The part of a review shared by every trigger path: run the selected
    reviewer on an already-`running` run, then persist findings and mark it
    succeeded, or record the error and mark it failed."""
    model = str(run.config_snapshot.get("model", _DEFAULT_MODEL))

    try:
        if agent == "cross_file":
            if not repo_path or storage is None:
                raise RuntimeError("agent='cross_file' requires a checkout of the repository")
            findings = await _run_cross_file(
                run=run, session=session, repo=repo, storage=storage, pr_title=pr_title,
                pr_body=pr_body, diff=diff, repo_path=repo_path, model=model,
            )
        else:
            result = await review_diff(
                pr_title=pr_title, pr_body=pr_body, diff=diff, model=model
            )
            run.tokens_in = result.tokens_in
            run.tokens_out = result.tokens_out
            run.cost_usd = result.cost_usd
            run.latency_ms = result.latency_ms
            findings = result.findings
    except Exception as exc:
        logger.exception("review of run %s failed", run.id)
        await mark_run_failed(session, run, exc)
        return

    for finding in findings:
        session.add(
            FindingRecord(
                run_id=run.id,
                file_path=finding.file_path,
                line_start=finding.line_start,
                line_end=finding.line_end,
                category=finding.category,
                severity=finding.severity,
                message=finding.message,
                evidence_json=[e.model_dump() for e in finding.evidence],
                confidence=finding.confidence,
                agent_name=finding.agent_name,
            )
        )

    run.status = AnalysisRunStatus.SUCCEEDED
    run.finished_at = datetime.now(UTC)
    await session.commit()


async def mark_run_running(session: AsyncSession, run: AnalysisRun) -> None:
    run.status = AnalysisRunStatus.RUNNING
    run.started_at = datetime.now(UTC)
    await session.commit()


async def mark_run_failed(session: AsyncSession, run: AnalysisRun, exc: BaseException) -> None:
    run.status = AnalysisRunStatus.FAILED
    run.error = str(exc)
    run.finished_at = datetime.now(UTC)
    await session.commit()


async def analyze_pr(ctx: dict[str, Any], run_id: str, org_id: str) -> None:
    """The manual (diff-paste) trigger path from `POST /analysis`. Always
    `diff_only`: it has no checkout to read. Only the run id and its
    organisation cross the queue; the session is bound to that organisation
    (row-level security), and the diff, title and body are read back from
    the run's own rows. A run id paired with the wrong organisation is
    simply not found. GitHub-connected repos use
    `worker.jobs.github.review_github_pr`.
    """
    session_factory = ctx["db_session_factory"]

    async with session_factory() as session:
        await bind_org(session, uuid.UUID(org_id))
        run = await session.get(AnalysisRun, uuid.UUID(run_id))
        if run is None:
            logger.error("analyze_pr: run %s no longer exists", run_id)
            return
        pr = await session.get(PullRequest, run.pr_id)
        repo = await session.get(Repository, pr.repo_id) if pr else None
        if pr is None or repo is None or not run.diff_text:
            await mark_run_failed(
                session, run, RuntimeError("pull request, repository or diff is gone")
            )
            return

        await mark_run_running(session, run)
        await execute_review(
            session, run, repo=repo, pr_title=pr.title, pr_body=pr.body or "",
            diff=run.diff_text, agent="diff_only", repo_path=None, storage=None,
        )
