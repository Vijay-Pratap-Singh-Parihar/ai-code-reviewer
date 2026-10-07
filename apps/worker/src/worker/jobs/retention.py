"""Data retention: delete what a disconnected connection or repository left
behind, either on request ("Delete data now") or once its retention period
has passed (`purge_expired_data`, run hourly by ARQ's cron).

Deleting a repository removes, in this order:

1. the `repositories` row, and through the composite foreign keys every
   pull request, run, finding, context bundle, branch index and index log
   under it, in one transaction, together with a `data.purged` audit entry;
2. after that commits, its clone and signed index blobs on disk.

Files go second so a failed transaction never leaves rows pointing at files
that no longer exist. If removing files fails, the per-organisation sweep in
`purge_expired_data` removes any directory whose repository no longer
exists, so nothing is left behind for long.

Everything runs under row-level security, one organisation at a time: the
job binds its session to each organisation in turn, so even the retention
job cannot touch two tenants' rows in one statement.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from db.audit import AuditAction, record_audit
from db.organization import GithubInstallation, Organization, User
from db.pull_request import AnalysisRun, FindingRecord, PullRequest
from db.repository import Repository
from db.tenancy import bind_org
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from worker.storage import TenantStorage, storage_from_ctx

logger = logging.getLogger(__name__)


def retention_days(ctx: dict[str, Any]) -> int:
    days: int = ctx.get("retention_days", 30)
    return days


async def _actor(session: AsyncSession, actor_id: str | None) -> uuid.UUID | None:
    """The requesting user, if they still exist: they may have been removed
    between the request and this job, and the deletion must still happen
    (and be recorded, as a system action)."""
    if not actor_id:
        return None
    user = await session.get(User, uuid.UUID(actor_id))
    return user.id if user else None


async def _is_actively_connected(session: AsyncSession, repo: Repository) -> bool:
    if repo.installation_id is None or repo.disconnected_at is not None:
        return False
    installation = await session.get(GithubInstallation, repo.installation_id)
    return installation is not None and installation.uninstalled_at is None


async def _remove_dirs(storage: TenantStorage, org_id: uuid.UUID, repo_id: uuid.UUID) -> None:
    for path in (storage.repo_cache_dir(org_id, repo_id), storage.index_dir(org_id, repo_id)):
        await asyncio.to_thread(shutil.rmtree, path, True)


async def _purge_repository(
    session: AsyncSession,
    storage: TenantStorage,
    repo: Repository,
    *,
    reason: str,
    actor_id: uuid.UUID | None,
) -> None:
    org_id, repo_id, name = repo.org_id, repo.id, repo.full_name
    runs = await session.scalar(
        select(func.count())
        .select_from(AnalysisRun)
        .join(PullRequest, AnalysisRun.pr_id == PullRequest.id)
        .where(PullRequest.repo_id == repo_id)
    )
    findings = await session.scalar(
        select(func.count())
        .select_from(FindingRecord)
        .join(AnalysisRun, FindingRecord.run_id == AnalysisRun.id)
        .join(PullRequest, AnalysisRun.pr_id == PullRequest.id)
        .where(PullRequest.repo_id == repo_id)
    )
    record_audit(
        session,
        org_id=org_id,
        actor_id=actor_id,
        action=AuditAction.DATA_PURGED,
        target=f"repository:{name}",
        metadata={
            "reason": reason,
            "repository_id": str(repo_id),
            "runs_deleted": int(runs or 0),
            "findings_deleted": int(findings or 0),
        },
    )
    await session.execute(delete(Repository).where(Repository.id == repo_id))
    await session.commit()
    await _remove_dirs(storage, org_id, repo_id)
    logger.info("purged repository %s (%s) of org %s: %s", name, repo_id, org_id, reason)


async def purge_repository_data(
    ctx: dict[str, Any], org_id: str, repo_id: str, actor_id: str | None = None
) -> None:
    """'Delete data now' for one repository (queued by `DELETE /repos/{id}/data`)."""
    storage = storage_from_ctx(ctx)
    async with ctx["db_session_factory"]() as session:
        await bind_org(session, uuid.UUID(org_id))
        repo = await session.get(Repository, uuid.UUID(repo_id))
        if repo is None:
            return
        # Re-checked here: it may have been connected again since the request.
        if await _is_actively_connected(session, repo):
            logger.warning("purge_repository_data: %s is connected again; skipping", repo_id)
            return
        await _purge_repository(
            session, storage, repo, reason="requested", actor_id=await _actor(session, actor_id)
        )


async def purge_installation_data(
    ctx: dict[str, Any], org_id: str, installation_id: str, actor_id: str | None = None
) -> None:
    """'Delete data now' for an uninstalled connection: all of its
    repositories, then the connection itself."""
    storage = storage_from_ctx(ctx)
    async with ctx["db_session_factory"]() as session:
        await bind_org(session, uuid.UUID(org_id))
        actor = await _actor(session, actor_id)
        installation = await session.get(GithubInstallation, uuid.UUID(installation_id))
        if installation is None or installation.uninstalled_at is None:
            return
        repos = list(
            await session.scalars(
                select(Repository).where(Repository.installation_id == installation.id)
            )
        )
        for repo in repos:
            await _purge_repository(session, storage, repo, reason="requested", actor_id=actor)
        await _delete_installation(session, installation, reason="requested", actor_id=actor)


async def _delete_installation(
    session: AsyncSession,
    installation: GithubInstallation,
    *,
    reason: str,
    actor_id: uuid.UUID | None,
) -> None:
    record_audit(
        session,
        org_id=installation.org_id,
        actor_id=actor_id,
        action=AuditAction.DATA_PURGED,
        target=f"installation:{installation.account_login}",
        metadata={"reason": reason, "installation_id": installation.installation_id},
    )
    await session.execute(
        delete(GithubInstallation).where(GithubInstallation.id == installation.id)
    )
    await session.commit()


async def _sweep_orphan_dirs(
    session: AsyncSession, storage: TenantStorage, org_id: uuid.UUID
) -> int:
    """Remove this organisation's clone/index directories whose repository
    row no longer exists (e.g. file removal failed after a purge)."""
    existing = {str(r) for r in await session.scalars(select(Repository.id))}
    removed = 0
    for root in (storage.repo_cache_root, storage.index_root):
        org_dir = root / str(org_id)
        if not org_dir.is_dir():
            continue
        for child in org_dir.iterdir():
            if child.is_dir() and _is_uuid(child.name) and child.name not in existing:
                await asyncio.to_thread(shutil.rmtree, child, True)
                removed += 1
    return removed


def _is_uuid(name: str) -> bool:
    try:
        uuid.UUID(name)
    except ValueError:
        return False
    return True


async def purge_expired_data(ctx: dict[str, Any]) -> dict[str, int]:
    """Hourly: purge every repository disconnected for longer than the
    retention period, then uninstalled connections with nothing left, one
    organisation at a time. A failure in one organisation is logged and the
    rest still run."""
    storage = storage_from_ctx(ctx)
    cutoff = datetime.now(UTC) - timedelta(days=retention_days(ctx))
    totals = {"repositories": 0, "installations": 0, "orphan_dirs": 0, "failed_orgs": 0}

    async with ctx["db_session_factory"]() as session:
        org_ids = list(await session.scalars(select(Organization.id)))

    for org_id in org_ids:
        try:
            async with ctx["db_session_factory"]() as session:
                await bind_org(session, org_id)
                expired = list(
                    await session.scalars(
                        select(Repository).where(
                            Repository.disconnected_at.is_not(None),
                            Repository.disconnected_at < cutoff,
                        )
                    )
                )
                for repo in expired:
                    await _purge_repository(
                        session, storage, repo, reason="retention", actor_id=None
                    )
                    totals["repositories"] += 1

                has_repos = select(Repository.id).where(
                    Repository.installation_id == GithubInstallation.id
                )
                empty_uninstalled = list(
                    await session.scalars(
                        select(GithubInstallation).where(
                            GithubInstallation.uninstalled_at.is_not(None),
                            GithubInstallation.uninstalled_at < cutoff,
                            ~has_repos.exists(),
                        )
                    )
                )
                for installation in empty_uninstalled:
                    await _delete_installation(
                        session, installation, reason="retention", actor_id=None
                    )
                    totals["installations"] += 1

                totals["orphan_dirs"] += await _sweep_orphan_dirs(session, storage, org_id)
        except Exception:
            logger.exception("purge_expired_data: organisation %s failed", org_id)
            totals["failed_orgs"] += 1

    if any(totals.values()):
        logger.info("purge_expired_data: %s", totals)
    return totals
