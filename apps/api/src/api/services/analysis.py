import uuid
from datetime import UTC, datetime

from db.organization import User
from db.provider import ModelTier
from db.pull_request import AnalysisRun, AnalysisRunStatus, PullRequest, PullRequestState
from db.repository import Repository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.schemas.analysis import AnalysisRequest
from api.services.providers import route_snapshot
from api.services.repositories import get_or_create_repository

__all__ = [
    "create_analysis_run",
    "get_run_for_org",
]


async def _get_or_create_pull_request(
    session: AsyncSession, *, repo: Repository, user: User, body: AnalysisRequest
) -> PullRequest:
    pr = await session.scalar(
        select(PullRequest).where(
            PullRequest.repo_id == repo.id, PullRequest.number == body.pr_number
        )
    )
    if pr is not None:
        pr.title = body.pr_title
        pr.body = body.pr_body
        pr.head_sha = body.head_sha
        pr.base_branch = body.base_branch
        return pr

    pr = PullRequest(
        repo_id=repo.id,
        org_id=repo.org_id,
        number=body.pr_number,
        title=body.pr_title,
        body=body.pr_body,
        author=user.email,
        base_branch=body.base_branch,
        head_sha=body.head_sha,
        state=PullRequestState.OPEN,
        opened_at=datetime.now(UTC),
    )
    session.add(pr)
    await session.flush()
    return pr


async def create_analysis_run(
    session: AsyncSession, *, user: User, body: AnalysisRequest
) -> AnalysisRun:
    # Fails (NoModelRouteError) before anything is written when the
    # organisation hasn't chosen a review model yet.
    model = await route_snapshot(session, user.org_id, ModelTier.REVIEW)
    repo = await get_or_create_repository(
        session, org_id=user.org_id, full_name=body.repo_full_name
    )
    pr = await _get_or_create_pull_request(session, repo=repo, user=user, body=body)

    run = AnalysisRun(
        pr_id=pr.id,
        org_id=pr.org_id,
        config_snapshot={
            "agent": body.agent,
            **model,
            "head_sha": body.head_sha,
            "source": "manual",
        },
        status=AnalysisRunStatus.QUEUED,
        diff_text=body.diff,
        created_at=datetime.now(UTC),
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def get_run_for_org(
    session: AsyncSession, *, run_id: uuid.UUID, org_id: uuid.UUID
) -> AnalysisRun | None:
    """Loads a run's findings only if it belongs to the caller's org — the
    multi-tenancy enforcement point the architecture doc calls for at the
    query layer, done here via a join rather than trusting a bare `run_id`.
    """
    stmt = (
        select(AnalysisRun)
        .join(PullRequest, AnalysisRun.pr_id == PullRequest.id)
        .join(Repository, PullRequest.repo_id == Repository.id)
        .options(
            selectinload(AnalysisRun.findings),
            selectinload(AnalysisRun.pull_request).selectinload(PullRequest.repository),
        )
        .where(AnalysisRun.id == run_id, Repository.org_id == org_id)
    )
    result: AnalysisRun | None = await session.scalar(stmt)
    return result
