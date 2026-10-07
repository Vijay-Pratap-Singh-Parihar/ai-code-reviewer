"""Repositories connected through the GitHub App: list them, toggle
auto-review, browse open PRs live from GitHub, and trigger a review or an
index build without the caller supplying a diff or a filesystem path."""

import uuid
from typing import Annotated

from db.audit import AuditAction, record_audit
from db.branch_index import BranchIndex
from db.organization import GithubInstallation
from db.pull_request import AnalysisRun, PullRequest
from db.repository import Repository
from fastapi import APIRouter, Depends, HTTPException, status
from ghapp import GitHubError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.deps import CurrentUser, OrgAdmin, RedisPool
from api.core.github import GitHub
from api.db.session import get_db
from api.schemas.analysis import AnalysisRunPublic
from api.schemas.branch_index import BranchIndexPublic
from api.schemas.github import (
    GitHubIndexRequest,
    GitHubReviewRequest,
    LatestRunPublic,
    PullRequestSummary,
    RepositoryPublic,
    RepositoryUpdate,
)
from api.services import github as github_service
from api.services import lifecycle
from api.services.branch_index import get_current_branch_index
from api.services.repositories import get_repository_for_org

router = APIRouter(prefix="/repos", tags=["repositories"])

Db = Annotated[AsyncSession, Depends(get_db)]


def _to_public(repo: Repository) -> RepositoryPublic:
    return RepositoryPublic(
        id=repo.id,
        full_name=repo.full_name,
        default_branch=repo.default_branch,
        is_active=repo.is_active,
        connected=(
            repo.installation_id is not None
            and repo.github_repo_id is not None
            and repo.disconnected_at is None
        ),
        auto_review_enabled=repo.auto_review_enabled,
        github_repo_id=repo.github_repo_id,
        installation_id=repo.installation_id,
        disconnected_at=repo.disconnected_at,
        purge_after=lifecycle.purge_after(repo.disconnected_at),
    )


async def _require_repo(db: AsyncSession, repo_id: uuid.UUID, org_id: uuid.UUID) -> Repository:
    repo = await get_repository_for_org(db, repo_id=repo_id, org_id=org_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repository not found")
    return repo


async def _installation_token(db: AsyncSession, gh: GitHub, repo: Repository) -> str:
    installation = (
        await db.get(GithubInstallation, repo.installation_id) if repo.installation_id else None
    )
    if installation is None or not repo.is_active:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "repository is not connected through the GitHub App"
        )
    try:
        return await gh.create_installation_token(installation.installation_id)
    except GitHubError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"GitHub request failed: {exc}") from exc


@router.get("", response_model=list[RepositoryPublic])
async def list_repositories(user: CurrentUser, db: Db) -> list[RepositoryPublic]:
    rows = await db.scalars(
        select(Repository)
        .where(Repository.org_id == user.org_id)
        .order_by(Repository.is_active.desc(), Repository.full_name)
    )
    return [_to_public(r) for r in rows]


@router.patch("/{repo_id}", response_model=RepositoryPublic)
async def update_repository(
    repo_id: uuid.UUID, body: RepositoryUpdate, user: OrgAdmin, db: Db
) -> RepositoryPublic:
    """Repository settings change what runs (and what is billed) for the
    whole organisation, so they are an owner/admin decision."""
    repo = await _require_repo(db, repo_id, user.org_id)
    if repo.auto_review_enabled != body.auto_review_enabled:
        record_audit(
            db,
            org_id=repo.org_id,
            actor_id=user.id,
            action=AuditAction.REPOSITORY_SETTINGS_CHANGED,
            target=f"repository:{repo.full_name}",
            metadata={"auto_review_enabled": body.auto_review_enabled},
        )
    repo.auto_review_enabled = body.auto_review_enabled
    await db.commit()
    return _to_public(repo)


@router.delete("/{repo_id}/data", status_code=status.HTTP_202_ACCEPTED)
async def delete_repository_data(
    repo_id: uuid.UUID, user: OrgAdmin, db: Db, redis: RedisPool
) -> dict[str, str]:
    """Delete a disconnected (or never connected) repository's data now:
    reviews, findings, indexes, clone and index files, and the row itself."""
    repo = await _require_repo(db, repo_id, user.org_id)
    try:
        await lifecycle.request_repository_purge(db, redis, repo=repo, actor=user)
    except lifecycle.StillConnectedError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "this repository is still connected through the GitHub App; remove it from the "
            "App's repository access on GitHub first",
        ) from exc
    return {"status": "queued"}


@router.get("/{repo_id}/pulls", response_model=list[PullRequestSummary])
async def list_pull_requests(
    repo_id: uuid.UUID, user: CurrentUser, db: Db, gh: GitHub
) -> list[PullRequestSummary]:
    repo = await _require_repo(db, repo_id, user.org_id)
    token = await _installation_token(db, gh, repo)
    try:
        pulls = await gh.list_open_pulls(token, repo.full_name)
    except GitHubError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"GitHub request failed: {exc}") from exc

    # Latest run per PR number, from our own records.
    latest: dict[int, AnalysisRun] = {}
    rows = await db.execute(
        select(PullRequest.number, AnalysisRun)
        .join(AnalysisRun, AnalysisRun.pr_id == PullRequest.id)
        .where(PullRequest.repo_id == repo.id)
        .order_by(AnalysisRun.created_at.desc())
    )
    for number, row_run in rows.tuples():
        latest.setdefault(number, row_run)

    summaries: list[PullRequestSummary] = []
    for pull in pulls:
        run = latest.get(int(pull["number"]))
        summaries.append(
            PullRequestSummary(
                number=int(pull["number"]),
                title=str(pull.get("title", "")),
                author=str((pull.get("user") or {}).get("login", "unknown")),
                head_sha=str(pull["head"]["sha"]),
                base_branch=str(pull["base"]["ref"]),
                draft=bool(pull.get("draft", False)),
                html_url=pull.get("html_url"),
                updated_at=pull.get("updated_at"),
                latest_run=(
                    LatestRunPublic(
                        id=run.id,
                        status=run.status,
                        agent=str(run.config_snapshot.get("agent", "diff_only")),
                        head_sha=str(run.config_snapshot.get("head_sha") or "") or None,
                    )
                    if run
                    else None
                ),
            )
        )
    return summaries


@router.post(
    "/{repo_id}/pulls/{number}/analysis",
    response_model=AnalysisRunPublic,
    status_code=status.HTTP_202_ACCEPTED,
)
async def review_pull_request(
    repo_id: uuid.UUID,
    number: int,
    body: GitHubReviewRequest,
    user: CurrentUser,
    db: Db,
    gh: GitHub,
    redis: RedisPool,
) -> AnalysisRunPublic:
    repo = await _require_repo(db, repo_id, user.org_id)
    token = await _installation_token(db, gh, repo)
    try:
        pull = await gh.get_pull(token, repo.full_name, number)
    except GitHubError as exc:
        code = status.HTTP_404_NOT_FOUND if exc.status_code == 404 else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(code, f"GitHub request failed: {exc}") from exc

    pr = await github_service.upsert_pull_request(db, repo, pull)

    branch_index_id: uuid.UUID | None = None
    if body.agent == "cross_file":
        view = await get_current_branch_index(db, repo_id=repo.id, branch_name=pr.base_branch)
        if view.ready is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"no ready branch index for '{repo.full_name}' @ '{pr.base_branch}'; "
                "build one first (POST /repos/{repo_id}/index)",
            )
        branch_index_id = view.ready.id

    run = await github_service.queue_github_review(
        db,
        redis,
        pr=pr,
        agent=body.agent,
        trigger="manual",
        branch_index_id=branch_index_id,
        actor_id=user.id,
        repo_full_name=repo.full_name,
    )
    return AnalysisRunPublic(
        id=run.id,
        status=run.status,
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        latency_ms=None,
        error=None,
        agent=body.agent,
        pr_number=pr.number,
        pr_title=pr.title,
        base_branch=pr.base_branch,
        head_sha=pr.head_sha,
        repo_full_name=repo.full_name,
    )


@router.post(
    "/{repo_id}/index", response_model=BranchIndexPublic, status_code=status.HTTP_202_ACCEPTED
)
async def index_repository(
    repo_id: uuid.UUID,
    body: GitHubIndexRequest,
    user: CurrentUser,
    db: Db,
    redis: RedisPool,
) -> BranchIndexPublic:
    repo = await _require_repo(db, repo_id, user.org_id)
    if repo.installation_id is None or not repo.is_active:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "repository is not connected through the GitHub App"
        )
    row: BranchIndex = await github_service.queue_github_index(
        db,
        redis,
        repo=repo,
        branch_name=body.branch_name or repo.default_branch,
        target_sha=None,
        force_full=body.force_full,
        actor_id=user.id,
    )
    return BranchIndexPublic.model_validate(row)
