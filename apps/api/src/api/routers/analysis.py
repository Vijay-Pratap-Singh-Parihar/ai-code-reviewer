import uuid
from typing import Annotated

from db.pull_request import AnalysisRun, FindingRecord, PullRequest
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.deps import CurrentUser, RedisPool
from api.db.session import get_db
from api.schemas.analysis import AnalysisRequest, AnalysisRunPublic, FindingPublic
from api.services import analysis as analysis_service

router = APIRouter(prefix="/analysis", tags=["analysis"])


def _to_public(
    run: AnalysisRun, findings: list[FindingRecord], pr: PullRequest, repo_full_name: str
) -> AnalysisRunPublic:
    return AnalysisRunPublic(
        id=run.id,
        status=run.status,
        tokens_in=run.tokens_in,
        tokens_out=run.tokens_out,
        cost_usd=float(run.cost_usd),
        latency_ms=run.latency_ms,
        error=run.error,
        findings=[FindingPublic.model_validate(f) for f in findings],
        agent=str(run.config_snapshot.get("agent", "diff_only")),
        repo_full_name=repo_full_name,
        pr_number=pr.number,
        pr_title=pr.title,
        base_branch=pr.base_branch,
        head_sha=str(run.config_snapshot.get("head_sha") or pr.head_sha),
        diff=run.diff_text,
    )


@router.post("", response_model=AnalysisRunPublic, status_code=status.HTTP_202_ACCEPTED)
async def trigger_analysis(
    body: AnalysisRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: RedisPool,
) -> AnalysisRunPublic:
    try:
        run = await analysis_service.create_analysis_run(db, user=user, body=body)
    except analysis_service.RepositoryOwnedByAnotherOrgError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"repository '{body.repo_full_name}' is already registered under a different "
            "organization",
        ) from exc
    except analysis_service.BranchIndexNotReadyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    await redis.enqueue_job(
        "analyze_pr",
        str(run.id),
        body.pr_title,
        body.pr_body,
        body.diff,
        body.agent,
        body.repo_path,
    )

    # A run this endpoint just created has no findings yet by definition —
    # accessing `run.findings` here would trigger a lazy load outside an
    # awaited context, which AsyncSession doesn't support.
    pr = await db.get(PullRequest, run.pr_id)
    assert pr is not None  # created in the same request
    return _to_public(run, findings=[], pr=pr, repo_full_name=body.repo_full_name)


@router.get("/{run_id}", response_model=AnalysisRunPublic)
async def get_analysis(
    run_id: uuid.UUID, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]
) -> AnalysisRunPublic:
    run = await analysis_service.get_run_for_org(db, run_id=run_id, org_id=user.org_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "analysis run not found")

    # get_run_for_org eager-loads findings, the PR and its repo, so this is safe.
    pr = run.pull_request
    return _to_public(run, findings=run.findings, pr=pr, repo_full_name=pr.repository.full_name)
