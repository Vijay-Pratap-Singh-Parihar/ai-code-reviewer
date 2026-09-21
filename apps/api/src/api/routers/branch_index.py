import uuid
from collections.abc import Sequence
from typing import Annotated

from db.branch_index import IndexUpdateLog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.deps import CurrentUser, RedisPool
from api.db.session import get_db
from api.schemas.branch_index import (
    BranchIndexPublic,
    BranchIndexStatusPublic,
    IndexTriggerRequest,
    IndexUpdateLogPublic,
)
from api.services import branch_index as branch_index_service
from api.services.analysis import RepositoryOwnedByAnotherOrgError
from api.services.branch_index import CurrentIndexView

router = APIRouter(prefix="/repos", tags=["branch-index"])


def _to_status_public(
    *,
    repo_id: uuid.UUID,
    branch_name: str,
    view: CurrentIndexView,
    recent_updates: Sequence[IndexUpdateLog],
) -> BranchIndexStatusPublic:
    ready = view.ready
    latest = view.latest_attempt
    recent_public = [IndexUpdateLogPublic.model_validate(log) for log in recent_updates]
    return BranchIndexStatusPublic(
        repo_id=repo_id,
        branch_name=branch_name,
        has_ready_index=ready is not None,
        head_sha=ready.head_sha if ready else None,
        node_count=ready.node_count if ready else 0,
        edge_count=ready.edge_count if ready else 0,
        unresolved_count=len(ready.unresolved_symbols) if ready else 0,
        build_duration_ms=ready.build_duration_ms if ready else None,
        built_at=ready.built_at if ready else None,
        ready_index_id=ready.id if ready else None,
        is_stale=view.is_stale,
        latest_attempt_status=latest.status if latest else None,
        latest_attempt_id=latest.id if latest else None,
        last_update=recent_public[0] if recent_public else None,
        recent_updates=recent_public,
    )


@router.post("/index", response_model=BranchIndexPublic, status_code=status.HTTP_202_ACCEPTED)
async def trigger_index(
    body: IndexTriggerRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: RedisPool,
) -> BranchIndexPublic:
    try:
        row = await branch_index_service.trigger_index_build(db, user=user, body=body)
    except RepositoryOwnedByAnotherOrgError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"repository '{body.repo_full_name}' is already registered under a different "
            "organization",
        ) from exc

    await redis.enqueue_job(
        "update_branch_index",
        str(row.id),
        body.repo_path,
        body.target_sha,
        body.force_full,
    )
    return BranchIndexPublic.model_validate(row)


@router.get("/{repo_id}/branches/{branch_name}/index", response_model=BranchIndexStatusPublic)
async def get_branch_index(
    repo_id: uuid.UUID,
    branch_name: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BranchIndexStatusPublic:
    view = await branch_index_service.get_branch_index_status(
        db, repo_id=repo_id, org_id=user.org_id, branch_name=branch_name
    )
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repository not found")

    recent_updates = await branch_index_service.get_recent_update_logs(
        db, repo_id=repo_id, branch_name=branch_name
    )
    return _to_status_public(
        repo_id=repo_id, branch_name=branch_name, view=view, recent_updates=recent_updates
    )
