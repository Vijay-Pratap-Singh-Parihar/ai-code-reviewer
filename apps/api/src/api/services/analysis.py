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
from api.services.branch_index import BranchIndexNotReadyError, get_current_branch_index
from api.services.repositories import RepositoryOwnedByAnotherOrgError, get_or_create_repository

settings = get_settings()

# Re-exported for backward compatibility: this module used to define both of
# these itself (Stage 3); they moved to `api.services.repositories` in Stage 5
# so `api.services.branch_index` could share them without importing this
# module's analysis-specific pull-request logic. Existing callers/tests that
# import `RepositoryOwnedByAnotherOrgError` from here still work unchanged.
# `BranchIndexNotReadyError` is re-exported the same way for `agent="cross_file"`
# requests (Stage 8's pipeline-wiring follow-up).
__all__ = [
    "BranchIndexNotReadyError",
    "RepositoryOwnedByAnotherOrgError",
    "create_analysis_run",
    "get_run_for_org",
]

_get_or_create_repository = get_or_create_repository


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

    branch_index_id: uuid.UUID | None = None
    if body.agent == "cross_file":
        view = await get_current_branch_index(
            session, repo_id=repo.id, branch_name=body.base_branch
        )
        if view.ready is None:
            raise BranchIndexNotReadyError(
                f"no ready branch index for '{body.repo_full_name}' @ '{body.base_branch}'; "
                "POST /repos/index for this branch and wait for it to reach 'ready' first"
            )
        branch_index_id = view.ready.id

    run = AnalysisRun(
        pr_id=pr.id,
        branch_index_id=branch_index_id,
        config_snapshot={"agent": body.agent, "model": settings.revu_model_review},
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
