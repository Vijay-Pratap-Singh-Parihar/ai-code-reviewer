"""ARQ job: build or update one `BranchIndex` row, end to end.

This is Stage 5's version of `worker.jobs.analyze.analyze_pr` — enqueued by
`POST /repos/index`, it does the actual git/indexing work that the API layer
can't (it needs a filesystem `repo_path` the worker process can read; see
`api.schemas.branch_index.IndexTriggerRequest`).

State machine (see `db.branch_index.BranchIndex`'s docstring for the full
design rationale):

1. The row already exists as `status=pending` (created by the API,
   `head_sha` possibly still `None`).
2. Resolve `target_sha` if the caller didn't pin one, flip to `building`,
   commit — from this point on this row is *not* "the current index" for
   any reader (`api.services.branch_index.get_current_branch_index` only
   ever returns `status=ready` rows), so nothing downstream can observe it
   half-written.
3. Decide full vs. incremental (`_decide_and_build`): first build ever for
   this branch, a manual `force_full`, a non-fast-forward update (force-push
   or rebase — detected via `revu.index.vcs.is_ancestor`), or a missing
   incremental prerequisite (previous result blob gone) each force a full
   rebuild, with the reason recorded on the `IndexUpdateLog` row; otherwise
   an incremental update reparses only the changed files.
4. Persist the graph blob + full `IndexResult` blob, write every column onto
   the row in one final update, flip to `ready`, write the `IndexUpdateLog`
   row, commit — all in the same transaction, so a reader never sees
   "ready" with stale/partial data.
5. Any exception anywhere in 2-4 marks the row `failed` (with a matching
   `IndexUpdateLog` row) rather than leaving it stuck on `building` forever
   or half-updating it.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from db.branch_index import BranchIndex, BranchIndexStatus, IndexUpdateLog, IndexUpdateMode
from db.repository import Repository
from revu.index import vcs
from revu.index.graph import IndexResult, build_index
from revu.index.incremental import incremental_update
from revu.index.store import (
    index_result_path,
    load_index_result,
    save_graph,
    save_index_result,
    to_branch_index_fields,
)
from sqlalchemy import select

logger = logging.getLogger(__name__)


def _decide_and_build(
    repo_path: Path,
    *,
    repo_identifier: str,
    branch_name: str,
    target_sha: str,
    previous: BranchIndex | None,
    force_full: bool,
) -> tuple[IndexUpdateMode, str | None, IndexResult, int]:
    """Pure (no DB, no ARQ) decision + build. Returns (mode, reason,
    index_result, files_changed). Kept synchronous and DB-free so it can run
    in a worker thread (`asyncio.to_thread`) and be unit-tested directly
    without a database.
    """
    if previous is None or previous.head_sha is None:
        result = build_index(repo_path, target_sha)
        return IndexUpdateMode.FULL, "first successful index build for this branch", result, (
            result.files_indexed
        )

    if force_full:
        result = build_index(repo_path, target_sha)
        return IndexUpdateMode.FULL, "manual full rebuild requested", result, result.files_indexed

    if previous.head_sha == target_sha:
        result = build_index(repo_path, target_sha)
        return (
            IndexUpdateMode.FULL,
            "re-index requested at the same head_sha as the current snapshot",
            result,
            result.files_indexed,
        )

    try:
        fast_forward = vcs.is_ancestor(repo_path, previous.head_sha, target_sha)
    except vcs.GitRefError as exc:
        result = build_index(repo_path, target_sha)
        return (
            IndexUpdateMode.FULL,
            f"could not verify fast-forward from the previous head ({exc}); full rebuild",
            result,
            result.files_indexed,
        )

    if not fast_forward:
        result = build_index(repo_path, target_sha)
        return (
            IndexUpdateMode.FULL,
            "non-fast-forward update detected (force-push or rebase); full rebuild",
            result,
            result.files_indexed,
        )

    try:
        previous_path = index_result_path(
            repo_identifier=repo_identifier, branch_name=branch_name, head_sha=previous.head_sha
        )
        previous_result = load_index_result(previous_path)
    except (OSError, TypeError) as exc:
        result = build_index(repo_path, target_sha)
        return (
            IndexUpdateMode.FULL,
            f"previous index snapshot unavailable ({exc}); full rebuild",
            result,
            result.files_indexed,
        )

    incremental_result = incremental_update(
        repo_path, previous.head_sha, target_sha, previous_result
    )
    return (
        IndexUpdateMode.INCREMENTAL,
        None,
        incremental_result.index,
        incremental_result.files_reparsed + incremental_result.files_removed,
    )


def _persist_index_result(
    result: IndexResult, *, repo_identifier: str, branch_name: str, head_sha: str
) -> str:
    graph_path = save_graph(
        result.graph, repo_identifier=repo_identifier, branch_name=branch_name, head_sha=head_sha
    )
    save_index_result(
        result, repo_identifier=repo_identifier, branch_name=branch_name, head_sha=head_sha
    )
    return str(graph_path)


async def update_branch_index(
    ctx: dict[str, Any],
    branch_index_id: str,
    repo_path: str,
    requested_sha: str | None,
    force_full: bool,
) -> None:
    session_factory = ctx["db_session_factory"]

    async with session_factory() as session:
        row = await session.get(BranchIndex, uuid.UUID(branch_index_id))
        if row is None:
            logger.error("update_branch_index: row %s no longer exists", branch_index_id)
            return

        repo = await session.get(Repository, row.repo_id)
        if repo is None:
            logger.error("update_branch_index: repository %s no longer exists", row.repo_id)
            row.status = BranchIndexStatus.FAILED
            row.updated_at = datetime.now(UTC)
            await session.commit()
            return

        repo_identifier = repo.full_name
        branch_name = row.branch_name
        path = Path(repo_path)

        try:
            target_sha: str
            if requested_sha:
                target_sha = requested_sha
            else:
                target_sha = await asyncio.to_thread(vcs.resolve_branch_head, path, branch_name)
        except Exception as exc:
            logger.exception("update_branch_index: could not resolve head for %s", branch_index_id)
            row.status = BranchIndexStatus.FAILED
            row.updated_at = datetime.now(UTC)
            session.add(
                IndexUpdateLog(
                    branch_index_id=row.id,
                    from_sha=None,
                    to_sha=requested_sha or "unknown",
                    files_changed=0,
                    mode=IndexUpdateMode.FULL,
                    duration_ms=0,
                    reason=f"could not resolve branch head: {exc}",
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
            return

        row.head_sha = target_sha
        row.status = BranchIndexStatus.BUILDING
        row.updated_at = datetime.now(UTC)
        await session.commit()

        previous = await session.scalar(
            select(BranchIndex)
            .where(
                BranchIndex.repo_id == row.repo_id,
                BranchIndex.branch_name == branch_name,
                BranchIndex.status == BranchIndexStatus.READY,
                BranchIndex.id != row.id,
            )
            .order_by(BranchIndex.created_at.desc())
            .limit(1)
        )

        try:
            mode, reason, index_result, files_changed = await asyncio.to_thread(
                _decide_and_build,
                path,
                repo_identifier=repo_identifier,
                branch_name=branch_name,
                target_sha=target_sha,
                previous=previous,
                force_full=force_full,
            )
        except Exception as exc:
            logger.exception("update_branch_index: build failed for %s", branch_index_id)
            row.status = BranchIndexStatus.FAILED
            row.updated_at = datetime.now(UTC)
            session.add(
                IndexUpdateLog(
                    branch_index_id=row.id,
                    from_sha=previous.head_sha if previous else None,
                    to_sha=target_sha,
                    files_changed=0,
                    mode=IndexUpdateMode.FULL if previous is None else IndexUpdateMode.INCREMENTAL,
                    duration_ms=0,
                    reason=f"build failed: {exc}",
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
            return

        graph_ref = await asyncio.to_thread(
            _persist_index_result,
            index_result,
            repo_identifier=repo_identifier,
            branch_name=branch_name,
            head_sha=target_sha,
        )

        fields = to_branch_index_fields(
            node_count=index_result.node_count,
            edge_count=index_result.edge_count,
            graph_ref=graph_ref,
            unresolved=[u.model_dump() for u in index_result.unresolved],
            build_duration_ms=index_result.duration_ms,
        )
        for key, value in fields.items():
            setattr(row, key, value)

        now = datetime.now(UTC)
        row.status = BranchIndexStatus.READY
        row.built_at = now
        row.updated_at = now

        carried_from_sha = (
            previous.head_sha if (previous and mode == IndexUpdateMode.INCREMENTAL) else None
        )
        session.add(
            IndexUpdateLog(
                branch_index_id=row.id,
                from_sha=carried_from_sha,
                to_sha=target_sha,
                files_changed=files_changed,
                mode=mode,
                duration_ms=index_result.duration_ms,
                reason=reason,
                created_at=now,
            )
        )
        await session.commit()
