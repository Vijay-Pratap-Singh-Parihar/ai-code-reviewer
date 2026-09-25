"""Branch-memory service: trigger a build and read back "the current index
for this branch" without ever exposing a half-written row.

See `db.branch_index.BranchIndex`'s docstring for the row-lifecycle design
this module implements (one row per build *attempt*, `status=ready` only
once every column is final) and `Product_Architecture_FullStack.md` §2 for
the product requirement ("index staleness is a first-class state ... never
analyse against a partially-written graph") it exists to satisfy.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from db.branch_index import BranchIndex, BranchIndexStatus, IndexUpdateLog
from db.organization import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.branch_index import IndexTriggerRequest
from api.services.repositories import get_or_create_repository, get_repository_for_org


class BranchIndexNotReadyError(Exception):
    """Raised when a `cross_file` analysis is requested for a (repo,
    base_branch) pair with no `ready` `BranchIndex` row yet — there is no
    graph to seed the agent's tools with. The caller must `POST /repos/index`
    for this branch first and wait for it to reach `ready` (or poll
    `GET /repos/{repo_id}/branches/{branch_name}/index`) before retrying.
    """


@dataclass
class CurrentIndexView:
    """The result of "what index should I use for (repo, branch) right now?"

    `ready` is the most recently *completed* successful build — the only row
    ever safe to hand to a graph consumer (Stage 6). It is `None` only when
    no build has ever succeeded for this branch yet.

    `latest_attempt` is the most recent row of *any* status, used purely for
    reporting freshness/progress (e.g. "a rebuild is in progress", "the last
    attempt failed") — never for its data columns, which may not be final.

    `is_stale` is True exactly when `latest_attempt` is a different, newer
    row than `ready` — i.e. there's a build in progress or a failed attempt
    that postdates the snapshot actually in use.
    """

    ready: BranchIndex | None
    latest_attempt: BranchIndex | None
    is_stale: bool


async def get_current_branch_index(
    session: AsyncSession, *, repo_id: uuid.UUID, branch_name: str
) -> CurrentIndexView:
    rows = (
        await session.scalars(
            select(BranchIndex)
            .where(BranchIndex.repo_id == repo_id, BranchIndex.branch_name == branch_name)
            .order_by(BranchIndex.created_at.desc())
        )
    ).all()

    if not rows:
        return CurrentIndexView(ready=None, latest_attempt=None, is_stale=False)

    latest_attempt = rows[0]
    ready = next((row for row in rows if row.status == BranchIndexStatus.READY), None)
    is_stale = ready is not None and latest_attempt.id != ready.id
    return CurrentIndexView(ready=ready, latest_attempt=latest_attempt, is_stale=is_stale)


async def get_recent_update_logs(
    session: AsyncSession, *, repo_id: uuid.UUID, branch_name: str, limit: int = 10
) -> list[IndexUpdateLog]:
    stmt = (
        select(IndexUpdateLog)
        .join(BranchIndex, IndexUpdateLog.branch_index_id == BranchIndex.id)
        .where(BranchIndex.repo_id == repo_id, BranchIndex.branch_name == branch_name)
        .order_by(IndexUpdateLog.created_at.desc())
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())


async def trigger_index_build(
    session: AsyncSession, *, user: User, body: IndexTriggerRequest
) -> BranchIndex:
    """Create the `pending` row a new build attempt starts life as. The
    actual git/indexing work — including resolving `target_sha` if the
    caller didn't pin one — happens worker-side (`worker.jobs.index_branch`),
    since that's the only place guaranteed to have `repo_path` on a
    filesystem it can read; see `IndexTriggerRequest`'s docstring.

    `created_at` is set explicitly (client-side, `datetime.now(UTC)`) rather
    than left to `CreatedAtMixin`'s `server_default=func.now()`: Postgres's
    `now()` is *transaction-scoped* (`transaction_timestamp()`), so several
    rows inserted inside one transaction — which happens routinely in tests
    that share a savepoint-per-request transaction, and could in principle
    happen in production for two builds triggered inside one longer-lived
    transaction — would otherwise all get an identical timestamp, making
    `get_current_branch_index`'s `ORDER BY created_at DESC` ambiguous between
    them. A Python-side timestamp advances per statement and keeps build
    attempts in the strict chronological order the staleness logic depends on.
    """
    repo = await get_or_create_repository(
        session, org_id=user.org_id, full_name=body.repo_full_name
    )

    row = BranchIndex(
        repo_id=repo.id,
        branch_name=body.branch_name,
        head_sha=body.target_sha,
        status=BranchIndexStatus.PENDING,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_branch_index_status(
    session: AsyncSession, *, repo_id: uuid.UUID, org_id: uuid.UUID, branch_name: str
) -> CurrentIndexView | None:
    """Org-scoped read: returns `None` if `repo_id` doesn't exist or belongs
    to another org (the router turns that into a 404), matching
    `analysis.get_run_for_org`'s "invisible, not a leaked 403" convention.
    """
    repo = await get_repository_for_org(session, repo_id=repo_id, org_id=org_id)
    if repo is None:
        return None
    return await get_current_branch_index(session, repo_id=repo_id, branch_name=branch_name)
