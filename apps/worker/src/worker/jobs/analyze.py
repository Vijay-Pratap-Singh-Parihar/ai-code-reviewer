import asyncio
import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from arq import Retry
from db.branch_index import BranchIndex
from db.organization import Organization
from db.pull_request import AnalysisRun, AnalysisRunStatus, FindingRecord, PullRequest
from db.repository import Repository
from db.tenancy import bind_org
from db.usage import BudgetExceededError, ensure_within_budget, record_run_usage
from observability import metrics
from revu.agents.cross_file import review_cross_file
from revu.agents.diff_only import review_diff
from revu.context.impact import blast_radius
from revu.index.store import load_graph
from revu.models import Finding, RunResult
from revu.providers.llm import ModelEndpoint, describe_error, is_transient
from revu.review_settings import (
    ReviewSettings,
    apply_output_limits,
    drop_ignored,
    estimate_review_tokens,
)
from revu.verify import verify_findings
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from worker.providers import ProviderAccess, endpoint_for_run, provider_access_from_ctx
from worker.storage import TenantStorage

logger = logging.getLogger(__name__)

# A review on a local model can take several minutes per part; arq's own
# 5-minute default would cancel it part-way.
REVIEW_JOB_TIMEOUT_S = 60 * 60
# Attempts for a review that keeps hitting transient provider errors,
# waiting RETRY_BASE_S, then twice that, between them.
REVIEW_MAX_TRIES = 3
RETRY_BASE_S = 30
# `fail_stuck_runs` leaves a running review this long past its time limit
# (arq may still be cleaning it up), and a queued one this long in all.
STUCK_GRACE_S = 15 * 60
STUCK_QUEUED_S = 6 * 60 * 60

INTERRUPTED = (
    "The review was interrupted before it finished (the worker restarted or the review hit "
    "its time limit). Run it again."
)


async def _run_cross_file(
    *, run: AnalysisRun, session: AsyncSession, repo: Repository, storage: TenantStorage,
    pr_title: str, pr_body: str, diff: str, repo_path: str, model: ModelEndpoint,
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
    run.blast_radius = _blast_radius(graph, diff, run_id=run.id)

    result = await review_cross_file(
        pr_title=pr_title, pr_body=pr_body, diff=diff,
        repo_root=repo_root, graph=graph, model=model,
    )

    verification = verify_findings(result.findings, repo_root=repo_root)
    run.config_snapshot = {**run.config_snapshot, "verification": asdict(verification.report)}
    _record_usage(run, result)
    return verification.findings


def _blast_radius(graph: Any, diff: str, *, run_id: uuid.UUID) -> dict[str, Any] | None:
    """What the change can affect, for the run page. Read off the graph, no
    model call; a failure here never fails the review."""
    try:
        return blast_radius(graph, diff)
    except Exception:
        logger.exception("blast radius for run %s could not be computed", run_id)
        return None


def _record_usage(run: AnalysisRun, result: RunResult) -> None:
    """Usage, plus what the run should tell its reader about how the review
    went: files left out, parts, and why it stopped short if it did."""
    run.tokens_in = result.tokens_in
    run.tokens_out = result.tokens_out
    run.cost_usd = result.cost_usd
    run.latency_ms = result.latency_ms
    notes: dict[str, Any] = {}
    if result.skipped_files:
        notes["skipped_files"] = result.skipped_files
    if result.parts > 1:
        notes["parts"] = result.parts
    if result.stopped_reason:
        notes["stopped_reason"] = result.stopped_reason
    if notes:
        run.config_snapshot = {**run.config_snapshot, **notes}



class OverCeilingError(Exception):
    """The review's estimated tokens exceed the repository's ceiling and
    nobody confirmed running it anyway."""


def _prepare_diff(run: AnalysisRun, diff: str, settings: ReviewSettings) -> str:
    """Apply the repository's ignore patterns, estimate the review's tokens
    before any model call, and stop at the token ceiling unless the run was
    confirmed ("Run anyway")."""
    ignored = drop_ignored(diff, settings.ignore_globs)
    estimate = estimate_review_tokens(ignored.diff)
    run.estimated_tokens = estimate
    if ignored.ignored:
        run.config_snapshot = {**run.config_snapshot, "ignored_files": ignored.ignored}
    ceiling = settings.max_tokens_per_review
    if ceiling and estimate > ceiling and not run.config_snapshot.get("allow_over_ceiling"):
        run.config_snapshot = {
            **run.config_snapshot,
            "over_ceiling": {"estimate": estimate, "ceiling": ceiling},
        }
        raise OverCeilingError(
            f"This review would use about {estimate:,} tokens, over this repository's limit of "
            f"{ceiling:,} per review. Nothing was sent to the model. Use \"Run anyway\" to "
            "review it, or raise the limit in the repository's settings."
        )
    return ignored.diff


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
    provider_access: ProviderAccess,
    attempt: int = 1,
) -> None:
    """The part of a review shared by every trigger path: run the selected
    reviewer on an already-`running` run, then persist findings and mark it
    succeeded, or record the error and mark it failed.

    A transient provider error (rate limiting, a dropped connection) on an
    attempt before the last puts the run back in the queue and raises arq's
    `Retry`, so the job runs again after a short wait. Interrupted (the
    job's time limit, or the worker shutting down), the run is marked failed
    with that reason rather than left "running".
    """
    settings = ReviewSettings.from_config(repo.config_json)
    try:
        # A run queued before the month's cap was reached doesn't start once
        # it is (see `db.usage`).
        await ensure_within_budget(session, run.org_id)
        diff = _prepare_diff(run, diff, settings)
        if not diff.strip():
            # Every changed file is ignored by the repository's settings.
            run.status = AnalysisRunStatus.SUCCEEDED
            run.finished_at = datetime.now(UTC)
            record_run_usage(session, run)
            await session.commit()
            return
        model = await endpoint_for_run(session, run, provider_access)
        if agent == "cross_file" and not model.supports_tools:
            # The cross-file agent works by calling tools; a model that can't
            # (found by "Test connection") gets the diff-only review instead,
            # and the run says so rather than failing or silently degrading.
            agent = "diff_only"
            run.config_snapshot = {
                **run.config_snapshot,
                "agent_fallback": "diff_only: the configured model can't call tools",
            }
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
            _record_usage(run, result)
            findings = result.findings
    except asyncio.CancelledError:
        logger.warning("review of run %s interrupted", run.id)
        metrics.REVIEWS.labels(agent, "interrupted").inc()
        await mark_run_failed(session, run, RuntimeError(INTERRUPTED))
        raise
    except BudgetExceededError as exc:
        logger.info("review of run %s not started: monthly budget reached", run.id)
        metrics.REVIEWS.labels(agent, "over_budget").inc()
        await mark_run_failed(session, run, exc)
        return
    except OverCeilingError as exc:
        logger.info("review of run %s not started: over the repository's token ceiling", run.id)
        metrics.REVIEWS.labels(agent, "over_ceiling").inc()
        await mark_run_failed(session, run, exc)
        return
    except Exception as exc:
        if is_transient(exc) and attempt < REVIEW_MAX_TRIES:
            wait_s = RETRY_BASE_S * 2 ** (attempt - 1)
            logger.warning(
                "review of run %s hit a transient error, retrying in %ds: %s", run.id, wait_s, exc
            )
            metrics.REVIEWS.labels(agent, "retried").inc()
            await mark_run_queued_for_retry(session, run, retries=attempt)
            raise Retry(defer=wait_s) from exc
        logger.exception("review of run %s failed", run.id)
        metrics.REVIEWS.labels(agent, "failed").inc()
        await mark_run_failed(session, run, exc)
        return

    limits = apply_output_limits(findings, settings)
    findings = limits.findings
    if limits.below_threshold or limits.over_cap:
        run.config_snapshot = {
            **run.config_snapshot,
            "hidden_below_threshold": limits.below_threshold,
            "hidden_over_cap": limits.over_cap,
            "min_severity": str(settings.min_severity),
        }

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
    record_run_usage(session, run)
    await session.commit()
    _count_success(agent, run, findings)


def _count_success(agent: str, run: AnalysisRun, findings: list[Finding]) -> None:
    metrics.REVIEWS.labels(agent, "succeeded").inc()
    if run.latency_ms is not None:
        metrics.REVIEW_DURATION.labels(agent).observe(run.latency_ms / 1000)
    metrics.REVIEW_TOKENS.labels("in").inc(run.tokens_in or 0)
    metrics.REVIEW_TOKENS.labels("out").inc(run.tokens_out or 0)
    metrics.REVIEW_COST.inc(float(run.cost_usd or 0))
    for finding in findings:
        metrics.REVIEW_FINDINGS.labels(str(finding.severity)).inc()


async def mark_run_running(session: AsyncSession, run: AnalysisRun) -> None:
    run.status = AnalysisRunStatus.RUNNING
    run.started_at = datetime.now(UTC)
    await session.commit()


async def mark_run_queued_for_retry(
    session: AsyncSession, run: AnalysisRun, *, retries: int
) -> None:
    run.status = AnalysisRunStatus.QUEUED
    run.config_snapshot = {**run.config_snapshot, "retries": retries}
    await session.commit()


async def mark_run_failed(session: AsyncSession, run: AnalysisRun, exc: BaseException) -> None:
    run.status = AnalysisRunStatus.FAILED
    run.error = describe_error(exc)
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
            provider_access=provider_access_from_ctx(ctx), attempt=ctx.get("job_try", 1),
        )


async def fail_stuck_runs(ctx: dict[str, Any]) -> int:
    """Cron: mark failed the runs nothing will ever finish, e.g. after a
    worker was killed mid-review: "running" for longer than a review job is
    allowed to take, or "queued" for longer than any queue should be.
    Returns how many it marked."""
    now = datetime.now(UTC)
    running_cutoff = now - timedelta(seconds=REVIEW_JOB_TIMEOUT_S + STUCK_GRACE_S)
    queued_cutoff = now - timedelta(seconds=STUCK_QUEUED_S)
    session_factory = ctx["db_session_factory"]
    async with session_factory() as session:
        org_ids = list(await session.scalars(select(Organization.id)))
    marked = 0
    for org_id in org_ids:
        async with session_factory() as session:
            await bind_org(session, org_id)
            stuck = await session.scalars(
                select(AnalysisRun).where(
                    or_(
                        and_(
                            AnalysisRun.status == AnalysisRunStatus.RUNNING,
                            AnalysisRun.started_at < running_cutoff,
                        ),
                        and_(
                            AnalysisRun.status == AnalysisRunStatus.QUEUED,
                            AnalysisRun.created_at < queued_cutoff,
                        ),
                    )
                )
            )
            for run in stuck:
                run.status = AnalysisRunStatus.FAILED
                run.error = INTERRUPTED
                run.finished_at = now
                agent = str(run.config_snapshot.get("agent", "diff_only"))
                metrics.REVIEWS.labels(agent, "interrupted").inc()
                marked += 1
            await session.commit()
    if marked:
        logger.warning("marked %d stuck runs failed", marked)
    return marked
