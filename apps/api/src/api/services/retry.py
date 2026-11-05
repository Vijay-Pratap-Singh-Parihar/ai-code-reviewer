"""Run a failed review again.

A retry is a new run of the same pull request with the same reviewer, not
the failed run reset: the failure stays in the history with its error, and
the new run records which run it retries (`retry_of`). It takes the
organisation's model as configured now, so fixing a provider on the AI
Providers screen and pressing Retry is the way out of a provider error.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from arq import ArqRedis
from db.audit import AuditAction, record_audit
from db.organization import User
from db.provider import ModelTier
from db.pull_request import AnalysisRun, AnalysisRunStatus, PullRequest
from db.usage import ensure_within_budget
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.queue import enqueue
from api.services import github as github_service
from api.services.branch_index import get_current_branch_index
from api.services.providers import route_snapshot

__all__ = ["RetryRefused", "retry_run"]


class RetryRefused(Exception):
    """The run can't be retried; the message says why, for the caller."""


async def retry_run(
    session: AsyncSession,
    redis: ArqRedis,
    *,
    run: AnalysisRun,
    user: User,
    allow_over_ceiling: bool = False,
) -> AnalysisRun:
    """Queue a new run retrying `run`, a failed run the caller's
    organisation owns (the caller loads it with `get_run_for_org`).
    Raises RetryRefused, or NoModelRouteError when no review model is
    configured any more."""
    if run.status != AnalysisRunStatus.FAILED:
        raise RetryRefused("only a failed review can be retried")
    pr: PullRequest = run.pull_request
    repo = pr.repository
    agent = str(run.config_snapshot.get("agent", "diff_only"))

    if run.config_snapshot.get("source") == "github":
        if not repo.is_active:
            raise RetryRefused("the repository is disconnected; reconnect it to review again")
        branch_index_id: uuid.UUID | None = None
        if agent == "cross_file":
            view = await get_current_branch_index(
                session, repo_id=repo.id, branch_name=pr.base_branch
            )
            if view.ready is None:
                raise RetryRefused(
                    f"no ready branch index for '{repo.full_name}' @ '{pr.base_branch}'; "
                    "build one first"
                )
            branch_index_id = view.ready.id
        return await github_service.queue_github_review(
            session,
            redis,
            pr=pr,
            agent=agent,
            trigger="retry",
            branch_index_id=branch_index_id,
            actor_id=user.id,
            repo_full_name=repo.full_name,
            retry_of=run.id,
            allow_over_ceiling=allow_over_ceiling,
        )

    # A pasted diff: review the same diff again.
    if not run.diff_text:
        raise RetryRefused("the failed review's diff is no longer stored")
    await ensure_within_budget(session, run.org_id)
    model = await route_snapshot(session, run.org_id, ModelTier.REVIEW)
    retry = AnalysisRun(
        pr_id=pr.id,
        org_id=run.org_id,
        config_snapshot={
            "agent": agent,
            **model,
            "head_sha": run.config_snapshot.get("head_sha") or pr.head_sha,
            "source": "manual",
            "retry_of": str(run.id),
            **({"allow_over_ceiling": True} if allow_over_ceiling else {}),
        },
        status=AnalysisRunStatus.QUEUED,
        diff_text=run.diff_text,
        created_at=datetime.now(UTC),
    )
    session.add(retry)
    await session.flush()
    record_audit(
        session,
        org_id=run.org_id,
        actor_id=user.id,
        action=AuditAction.REVIEW_REQUESTED,
        target=f"pull_request:{repo.full_name}#{pr.number}",
        metadata={
            "run_id": str(retry.id),
            "agent": agent,
            "trigger": "retry",
            "retry_of": str(run.id),
        },
    )
    await session.commit()
    await enqueue(redis, "analyze_pr", str(retry.id), str(retry.org_id))
    return retry
