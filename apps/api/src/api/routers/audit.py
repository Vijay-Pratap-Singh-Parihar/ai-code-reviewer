"""The organisation's audit trail, newest first. Owners and admins only."""

from datetime import datetime
from typing import Annotated

from db.ledger import AuditLog
from db.organization import User
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.deps import OrgAdmin
from api.db.session import get_db
from api.schemas.audit import AuditEntryPublic, AuditPage

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=AuditPage)
async def list_audit_entries(
    user: OrgAdmin,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: datetime | None = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
) -> AuditPage:
    stmt = (
        select(AuditLog, User.email)
        .outerjoin(User, User.id == AuditLog.actor_id)
        .where(AuditLog.org_id == user.org_id)
        .order_by(AuditLog.at.desc(), AuditLog.id.desc())
        .limit(limit + 1)
    )
    if before is not None:
        stmt = stmt.where(AuditLog.at < before)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = (await db.execute(stmt)).all()

    entries = [
        AuditEntryPublic(
            id=entry.id,
            at=entry.at,
            action=entry.action,
            target=entry.target,
            actor_id=entry.actor_id,
            actor_email=email,
            metadata=entry.extra,
        )
        for entry, email in rows[:limit]
    ]
    next_before = entries[-1].at if len(rows) > limit else None
    return AuditPage(entries=entries, next_before=next_before)
