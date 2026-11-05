"""Usage & Budget: this month's spend and tokens against the organisation's
caps. Anyone in the organisation can see it; owners and admins set the caps."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.deps import CurrentUser, OrgAdmin
from api.db.session import get_db
from api.schemas.usage import BudgetPublic, BudgetUpdate, UsagePublic
from api.services import usage as usage_service

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("", response_model=UsagePublic)
async def get_usage(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]) -> UsagePublic:
    return await usage_service.get_usage(db, user.org_id)


@router.put("/budget", response_model=BudgetPublic)
async def set_budget(
    body: BudgetUpdate, user: OrgAdmin, db: Annotated[AsyncSession, Depends(get_db)]
) -> BudgetPublic:
    return await usage_service.set_budget(db, user=user, body=body)
