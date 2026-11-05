"""Branch-memory service: read back "the current index for this branch"
without ever exposing a half-written row. Builds are queued from
`api.services.github.queue_github_index` for GitHub-connected repos.

See `db.branch_index.BranchIndex`'s docstring for the row-lifecycle design
this module implements (one row per build *attempt*, `status=ready` only
once every column is final) and `Product_Architecture_FullStack.md` §2 for
the product requirement ("index staleness is a first-class state ... never
analyse against a partially-written graph") it exists to satisfy.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from db.branch_index import BranchIndex, BranchIndexStatus, IndexUpdateLog, IndexUpdateMode
from db.repository import Repository
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.branch_index import BranchMemoryRow, IndexUpdateLogPublic
from api.services.repositories import get_repository_for_org


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


# `revu.index.store.to_branch_index_fields` keeps at most this many.
UNRESOLVED_CAP = 500
UPDATE_WINDOW = timedelta(days=30)
_IN_FLIGHT = {BranchIndexStatus.PENDING, BranchIndexStatus.BUILDING}
BranchState = Literal["ready", "building", "failed", "not_indexed"]


async def list_branch_memory(
    session: AsyncSession, org_id: uuid.UUID, *, now: datetime | None = None
) -> list[BranchMemoryRow]:
    """Every indexed branch of the organisation's GitHub repositories, plus
    the default branch of each repository that has never been indexed, so
    it can be built from the same screen."""
    now = now or datetime.now(UTC)
    repos = (
        await session.scalars(
            select(Repository)
            .where(Repository.org_id == org_id, Repository.github_repo_id.is_not(None))
            .order_by(Repository.full_name)
        )
    ).all()

    # Attempts newest first, without their (possibly large) unresolved lists.
    attempts = (
        await session.execute(
            select(
                BranchIndex.repo_id,
                BranchIndex.branch_name,
                BranchIndex.status,
                BranchIndex.head_sha,
                BranchIndex.node_count,
                BranchIndex.edge_count,
                BranchIndex.build_duration_ms,
                BranchIndex.built_at,
                func.jsonb_array_length(BranchIndex.unresolved_symbols).label("unresolved"),
            )
            .where(BranchIndex.org_id == org_id)
            .order_by(BranchIndex.created_at.desc())
        )
    ).all()
    latest: dict[tuple[uuid.UUID, str], Any] = {}
    ready: dict[tuple[uuid.UUID, str], Any] = {}
    for row in attempts:
        key = (row.repo_id, row.branch_name)
        latest.setdefault(key, row)
        if row.status == BranchIndexStatus.READY:
            ready.setdefault(key, row)

    logs = (
        await session.execute(
            select(BranchIndex.repo_id, BranchIndex.branch_name, IndexUpdateLog)
            .join(BranchIndex, BranchIndex.id == IndexUpdateLog.branch_index_id)
            .where(IndexUpdateLog.org_id == org_id)
            .order_by(IndexUpdateLog.created_at.desc())
        )
    ).all()
    last_log: dict[tuple[uuid.UUID, str], IndexUpdateLog] = {}
    recent: dict[tuple[uuid.UUID, str], dict[IndexUpdateMode, int]] = {}
    for repo_id, branch_name, log in logs:
        key = (repo_id, branch_name)
        last_log.setdefault(key, log)
        if log.created_at >= now - UPDATE_WINDOW:
            modes = recent.setdefault(key, {})
            modes[log.mode] = modes.get(log.mode, 0) + 1

    rows: list[BranchMemoryRow] = []
    for repo in repos:
        branches = sorted(
            {b for r, b in latest if r == repo.id} | {repo.default_branch},
            key=lambda b: (b != repo.default_branch, b),
        )
        for branch in branches:
            key = (repo.id, branch)
            newest, used, log = latest.get(key), ready.get(key), last_log.get(key)
            state: BranchState
            if newest is None:
                state = "not_indexed"
            elif newest.status in _IN_FLIGHT:
                state = "building"
            elif newest.status == BranchIndexStatus.FAILED:
                state = "failed"
            else:
                state = "ready"
            unresolved = int(used.unresolved or 0) if used else 0
            references = (used.edge_count + unresolved) if used else 0
            modes = recent.get(key, {})
            rows.append(
                BranchMemoryRow(
                    repo_id=repo.id,
                    repo_full_name=repo.full_name,
                    repo_active=repo.is_active,
                    branch_name=branch,
                    is_default_branch=branch == repo.default_branch,
                    state=state,
                    has_ready_index=used is not None,
                    head_sha=used.head_sha if used else None,
                    node_count=used.node_count if used else 0,
                    edge_count=used.edge_count if used else 0,
                    unresolved_count=unresolved,
                    unresolved_capped=unresolved >= UNRESOLVED_CAP,
                    unresolved_rate=(
                        round(unresolved / references, 4) if used and references else None
                    ),
                    build_duration_ms=used.build_duration_ms if used else None,
                    built_at=used.built_at if used else None,
                    last_update=IndexUpdateLogPublic.model_validate(log) if log else None,
                    full_rebuilds_30d=modes.get(IndexUpdateMode.FULL, 0),
                    incremental_updates_30d=modes.get(IndexUpdateMode.INCREMENTAL, 0),
                )
            )
    return rows
