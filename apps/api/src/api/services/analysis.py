import uuid
from datetime import UTC, datetime

from db.organization import User
from db.pull_request import AnalysisRun, AnalysisRunStatus, PullRequest, PullRequestState
from db.repository import Repository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.core.config import get_settings
from api.schemas.analysis import AnalysisRequest

settings = get_settings()


class RepositoryOwnedByAnotherOrgError(Exception):
    """`repositories.full_name` is globally unique because in the real
    GitHub App model (Stage 10), a given repo can only be connected to one
    org's installation. Until then, this ad-hoc endpoint has no GitHub
    verification to enforce that naturally, so it must be checked explicitly
    — silently reusing another org's row would misattribute the run in a way
    that's invisible until the caller tries to fetch it back and gets a
    confusing 404.
    """


async def _get_or_create_repository(
    session: AsyncSession, *, org_id: uuid.UUID, full_name: str
) -> Repository:
    repo = await session.scalar(select(Repository).where(Repository.full_name == full_name))
    if repo is not None:
        if repo.org_id != org_id:
            raise RepositoryOwnedByAnotherOrgError(full_name)
        return repo

    repo = Repository(org_id=org_id, full_name=full_name)
    session.add(repo)
    await session.flush()
    return repo


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
    repo = await _get_or_create_repository(
        session, org_id=user.org_id, full_name=body.repo_full_name
    )
    pr = await _get_or_create_pull_request(session, repo=repo, user=user, body=body)

    run = AnalysisRun(
        pr_id=pr.id,
        config_snapshot={"agent": "diff_only", "model": settings.revu_model_review},
        status=AnalysisRunStatus.QUEUED,
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
        .options(selectinload(AnalysisRun.findings))
        .where(AnalysisRun.id == run_id, Repository.org_id == org_id)
    )
    result: AnalysisRun | None = await session.scalar(stmt)
    return result
