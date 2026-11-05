"""Branch Memory: every indexed branch in the organisation at a glance.
Builds are requested with `POST /repos/{repo_id}/index`."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.deps import CurrentUser
from api.db.session import get_db
from api.schemas.branch_index import BranchMemoryRow
from api.services.branch_index import list_branch_memory

router = APIRouter(prefix="/branch-memory", tags=["branch-index"])


@router.get("", response_model=list[BranchMemoryRow])
async def get_branch_memory(
    user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]
) -> list[BranchMemoryRow]:
    return await list_branch_memory(db, user.org_id)
