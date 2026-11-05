"""History: the organisation's past reviews, filterable, and adoption
statistics. Anyone in the organisation can read it."""

import uuid
from datetime import datetime
from typing import Annotated

from db.pull_request import AnalysisRunStatus, PullRequestState
from fastapi import APIRouter, Depends, Query
from revu.models import Severity
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.deps import CurrentUser
from api.db.session import get_db
from api.schemas.history import AdoptionStats, HistoryPage
from api.services import history as history_service

router = APIRouter(prefix="/history", tags=["history"])


@router.get("", response_model=HistoryPage)
async def list_history(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    repo_id: uuid.UUID | None = None,
    severity: Annotated[
        Severity | None, Query(description="runs with a finding at this severity or above")
    ] = None,
    outcome: AnalysisRunStatus | None = None,
    pr_state: PullRequestState | None = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> HistoryPage:
    return await history_service.list_history(
        db,
        user.org_id,
        repo_id=repo_id,
        severity=severity,
        outcome=outcome,
        pr_state=pr_state,
        before=before,
        limit=limit,
    )


@router.get("/stats", response_model=AdoptionStats)
async def adoption_stats(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    repo_id: uuid.UUID | None = None,
) -> AdoptionStats:
    return await history_service.adoption_stats(db, user.org_id, repo_id=repo_id)
