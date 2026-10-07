"""Data retention jobs: what gets deleted, what is kept, and that it is all
recorded. Runs under row-level security as the application role, like the
worker itself."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from db.audit import AuditAction
from db.ledger import AuditLog
from db.organization import GithubInstallation, Organization, User
from db.pull_request import (
    AnalysisRun,
    AnalysisRunStatus,
    FindingRecord,
    PullRequest,
    PullRequestState,
)
from db.repository import Repository
from db.tenancy import bind_org
from revu.models import FindingCategory, Severity
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from worker.jobs import retention
from worker.storage import TenantStorage

NOW = datetime.now(UTC)
LONG_AGO = NOW - timedelta(days=31)
RECENTLY = NOW - timedelta(days=1)


async def _org(session: AsyncSession, name: str) -> uuid.UUID:
    org = Organization(name=name)
    session.add(org)
    await session.flush()
    await bind_org(session, org.id)
    return org.id


async def _installation(
    session: AsyncSession, org_id: uuid.UUID, *, uninstalled_at: datetime | None = None
) -> GithubInstallation:
    installation = GithubInstallation(
        org_id=org_id,
        installation_id=int(uuid.uuid4().int % 10**12),
        account_login="acme",
        installed_at=NOW - timedelta(days=90),
        uninstalled_at=uninstalled_at,
    )
    session.add(installation)
    await session.flush()
    return installation


async def _repo_with_history(
    session: AsyncSession,
    storage: TenantStorage,
    org_id: uuid.UUID,
    name: str,
    *,
    installation: GithubInstallation | None = None,
    disconnected_at: datetime | None = None,
) -> Repository:
    """A repository with a PR, a run, a finding, and files on disk."""
    repo = Repository(
        org_id=org_id,
        full_name=name,
        installation_id=installation.id if installation else None,
        github_repo_id=int(uuid.uuid4().int % 10**9),
        is_active=disconnected_at is None,
        disconnected_at=disconnected_at,
    )
    session.add(repo)
    await session.flush()
    pr = PullRequest(
        repo_id=repo.id,
        number=1,
        title="t",
        author="a",
        base_branch="main",
        head_sha="a" * 40,
        state=PullRequestState.OPEN,
        opened_at=NOW,
    )
    session.add(pr)
    await session.flush()
    run = AnalysisRun(pr_id=pr.id, status=AnalysisRunStatus.SUCCEEDED, config_snapshot={})
    session.add(run)
    await session.flush()
    session.add(
        FindingRecord(
            run_id=run.id,
            file_path="a.py",
            line_start=1,
            line_end=1,
            category=FindingCategory.CORRECTNESS,
            severity=Severity.HIGH,
            message="m",
            confidence=0.9,
            agent_name="diff_only",
        )
    )
    await session.commit()
    for directory in (
        storage.repo_cache_dir(org_id, repo.id),
        storage.index_dir(org_id, repo.id),
    ):
        directory.mkdir(parents=True)
        (directory / "blob").write_text("x")
    return repo


async def _count(session: AsyncSession, model: Any) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _audit(session: AsyncSession) -> list[AuditLog]:
    return list(await session.scalars(select(AuditLog).order_by(AuditLog.at)))


async def test_purge_repository_data_deletes_rows_files_and_records_it(
    worker_db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    tenant_storage: TenantStorage,
) -> None:
    org_id = await _org(worker_db_session, "Acme")
    repo = await _repo_with_history(
        worker_db_session, tenant_storage, org_id, "acme/old", disconnected_at=RECENTLY
    )
    user = User(org_id=org_id, email="admin@acme.test", password_hash="x")
    worker_db_session.add(user)
    await worker_db_session.flush()
    actor = user.id

    await retention.purge_repository_data(worker_ctx, str(org_id), str(repo.id), str(actor))

    await bind_org(worker_db_session, org_id)
    for model in (Repository, PullRequest, AnalysisRun, FindingRecord):
        assert await _count(worker_db_session, model) == 0, model.__name__
    assert not tenant_storage.repo_cache_dir(org_id, repo.id).exists()
    assert not tenant_storage.index_dir(org_id, repo.id).exists()
    [entry] = await _audit(worker_db_session)
    assert entry.action == AuditAction.DATA_PURGED
    assert entry.actor_id == actor
    assert entry.extra["reason"] == "requested"
    assert (entry.extra["runs_deleted"], entry.extra["findings_deleted"]) == (1, 1)


async def test_purge_repository_data_leaves_a_repository_that_was_reconnected(
    worker_db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    tenant_storage: TenantStorage,
) -> None:
    org_id = await _org(worker_db_session, "Acme")
    installation = await _installation(worker_db_session, org_id)
    repo = await _repo_with_history(
        worker_db_session, tenant_storage, org_id, "acme/back", installation=installation
    )

    await retention.purge_repository_data(worker_ctx, str(org_id), str(repo.id), None)

    await bind_org(worker_db_session, org_id)
    assert await _count(worker_db_session, Repository) == 1
    assert tenant_storage.repo_cache_dir(org_id, repo.id).exists()


async def test_purge_installation_data_removes_the_connection_and_all_its_repositories(
    worker_db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    tenant_storage: TenantStorage,
) -> None:
    org_id = await _org(worker_db_session, "Acme")
    installation = await _installation(worker_db_session, org_id, uninstalled_at=RECENTLY)
    for name in ("acme/one", "acme/two"):
        await _repo_with_history(
            worker_db_session,
            tenant_storage,
            org_id,
            name,
            installation=installation,
            disconnected_at=RECENTLY,
        )

    await retention.purge_installation_data(worker_ctx, str(org_id), str(installation.id), None)

    await bind_org(worker_db_session, org_id)
    assert await _count(worker_db_session, Repository) == 0
    assert await _count(worker_db_session, GithubInstallation) == 0
    actions = [e.action for e in await _audit(worker_db_session)]
    assert actions == [AuditAction.DATA_PURGED] * 3


async def test_retention_job_purges_only_what_has_expired_in_every_organisation(
    worker_db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    tenant_storage: TenantStorage,
) -> None:
    org_a = await _org(worker_db_session, "Alpha")
    gone = await _installation(worker_db_session, org_a, uninstalled_at=LONG_AGO)
    expired_a = await _repo_with_history(
        worker_db_session,
        tenant_storage,
        org_a,
        "alpha/expired",
        installation=gone,
        disconnected_at=LONG_AGO,
    )
    recent_a = await _repo_with_history(
        worker_db_session, tenant_storage, org_a, "alpha/recent", disconnected_at=RECENTLY
    )
    live = await _installation(worker_db_session, org_a)
    connected_a = await _repo_with_history(
        worker_db_session, tenant_storage, org_a, "alpha/live", installation=live
    )
    orphan = tenant_storage.repo_cache_dir(org_a, uuid.uuid4())
    orphan.mkdir(parents=True)

    org_b = await _org(worker_db_session, "Bravo")
    expired_b = await _repo_with_history(
        worker_db_session, tenant_storage, org_b, "bravo/expired", disconnected_at=LONG_AGO
    )

    totals = await retention.purge_expired_data(worker_ctx)

    assert totals == {"repositories": 2, "installations": 1, "orphan_dirs": 1, "failed_orgs": 0}
    await bind_org(worker_db_session, org_a)
    remaining = set(await worker_db_session.scalars(select(Repository.full_name)))
    assert remaining == {"alpha/recent", "alpha/live"}
    assert [i.id for i in await worker_db_session.scalars(select(GithubInstallation))] == [live.id]
    assert not tenant_storage.repo_cache_dir(org_a, expired_a.id).exists()
    assert tenant_storage.repo_cache_dir(org_a, recent_a.id).exists()
    assert tenant_storage.repo_cache_dir(org_a, connected_a.id).exists()
    assert not orphan.exists()
    await bind_org(worker_db_session, org_b)
    assert await _count(worker_db_session, Repository) == 0
    assert not tenant_storage.index_dir(org_b, expired_b.id).exists()
    purged = [e for e in await _audit(worker_db_session) if e.action == AuditAction.DATA_PURGED]
    assert [e.extra["reason"] for e in purged] == ["retention"]


async def test_a_deletion_requested_by_a_since_removed_user_still_happens(
    worker_db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    tenant_storage: TenantStorage,
) -> None:
    org_id = await _org(worker_db_session, "Acme")
    repo = await _repo_with_history(
        worker_db_session, tenant_storage, org_id, "acme/old", disconnected_at=RECENTLY
    )

    await retention.purge_repository_data(worker_ctx, str(org_id), str(repo.id), str(uuid.uuid4()))

    await bind_org(worker_db_session, org_id)
    assert await _count(worker_db_session, Repository) == 0
    [entry] = await _audit(worker_db_session)
    assert entry.actor_id is None
