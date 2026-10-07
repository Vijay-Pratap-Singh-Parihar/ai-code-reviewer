"""Data lifecycle for connections and repositories, API side.

The worker owns the actual deletion (it holds the clones and index files on
its volume); the API decides what may be deleted, records the request in
the audit log and queues the job. See `worker.jobs.retention`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from arq import ArqRedis
from db.audit import AuditAction, record_audit
from db.organization import GithubInstallation, User
from db.repository import Repository
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings


class StillConnectedError(Exception):
    """Deleting data that GitHub would immediately sync back is refused:
    the repository (or installation) must be disconnected on GitHub first."""


def purge_after(disconnected_at: datetime | None) -> datetime | None:
    if disconnected_at is None:
        return None
    return disconnected_at + timedelta(days=get_settings().revu_disconnected_retention_days)


async def is_actively_connected(session: AsyncSession, repo: Repository) -> bool:
    if repo.installation_id is None or repo.disconnected_at is not None:
        return False
    installation = await session.get(GithubInstallation, repo.installation_id)
    return installation is not None and installation.uninstalled_at is None


async def request_repository_purge(
    session: AsyncSession, redis: ArqRedis, *, repo: Repository, actor: User
) -> None:
    if await is_actively_connected(session, repo):
        raise StillConnectedError(repo.full_name)
    record_audit(
        session,
        org_id=repo.org_id,
        actor_id=actor.id,
        action=AuditAction.DATA_DELETION_REQUESTED,
        target=f"repository:{repo.full_name}",
        metadata={"repository_id": str(repo.id)},
    )
    await session.commit()
    await redis.enqueue_job(
        "purge_repository_data", str(repo.org_id), str(repo.id), str(actor.id)
    )


async def request_installation_purge(
    session: AsyncSession,
    redis: ArqRedis,
    *,
    installation: GithubInstallation,
    actor: User,
) -> None:
    if installation.uninstalled_at is None:
        raise StillConnectedError(installation.account_login)
    record_audit(
        session,
        org_id=installation.org_id,
        actor_id=actor.id,
        action=AuditAction.DATA_DELETION_REQUESTED,
        target=f"installation:{installation.account_login}",
        metadata={"installation_id": installation.installation_id},
    )
    await session.commit()
    await redis.enqueue_job(
        "purge_installation_data",
        str(installation.org_id),
        str(installation.id),
        str(actor.id),
    )


def installation_status(installation: GithubInstallation) -> str:
    if installation.uninstalled_at is not None:
        return "uninstalled"
    if installation.suspended_at is not None:
        return "suspended"
    return "active"

