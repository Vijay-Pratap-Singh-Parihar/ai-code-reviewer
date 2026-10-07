"""Proof that Postgres itself keeps organisations apart, not just our WHERE clauses.

Each test seeds two organisations' data as the schema owner, then switches
the same transaction to the application role (`SET ROLE revu_app`, what the
API and worker connect as) and issues deliberately *unfiltered* queries. If
row-level security were missing or misconfigured on any table, these
queries would see, change or create the other organisation's rows.

Everything runs in one transaction that is rolled back, like the rest of
the suite.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from db.branch_index import BranchIndex, BranchIndexStatus, IndexUpdateLog, IndexUpdateMode
from db.ledger import AuditLog, TokenUsageLedger
from db.organization import GithubInstallation, Organization
from db.provider import AIProvider, ModelRoute, ModelTier, ProviderKind
from db.pull_request import (
    AnalysisRun,
    AnalysisRunStatus,
    ContextBundleRecord,
    FindingRecord,
    PullRequest,
    PullRequestState,
)
from db.repository import Repository, TrackedBranch
from db.tenancy import APP_ROLE, TENANT_TABLES
from revu.models import FindingCategory, Severity
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


@dataclass
class Tenant:
    org_id: object
    repo_id: object
    installation_github_id: int


def _seed_tenant(session: Session, name: str, installation_github_id: int) -> Tenant:
    """One organisation with a row in every tenant table. Child rows omit
    `org_id` on purpose: the inherit trigger fills it in from the parent."""
    org = Organization(name=name)
    session.add(org)
    session.flush()

    installation = GithubInstallation(
        org_id=org.id,
        installation_id=installation_github_id,
        account_login=name.lower(),
        installed_at=datetime.now(UTC),
    )
    session.add(installation)
    session.flush()
    # The same GitHub repository name in both organisations: separate copies.
    repo = Repository(org_id=org.id, full_name="shared/repo", installation_id=installation.id)
    session.add(repo)
    session.flush()

    session.add(TrackedBranch(repo_id=repo.id, branch_name="main"))
    pr = PullRequest(
        repo_id=repo.id,
        number=1,
        title=f"{name} PR",
        author="dev",
        base_branch="main",
        head_sha="a" * 40,
        state=PullRequestState.OPEN,
        opened_at=datetime.now(UTC),
    )
    index = BranchIndex(
        repo_id=repo.id,
        branch_name="main",
        status=BranchIndexStatus.READY,
        created_at=datetime.now(UTC),
    )
    session.add_all([pr, index])
    session.flush()

    run = AnalysisRun(pr_id=pr.id, status=AnalysisRunStatus.SUCCEEDED, config_snapshot={})
    session.add(run)
    session.add(
        IndexUpdateLog(
            branch_index_id=index.id,
            to_sha="a" * 40,
            mode=IndexUpdateMode.FULL,
            duration_ms=1,
            created_at=datetime.now(UTC),
        )
    )
    session.flush()

    session.add_all(
        [
            FindingRecord(
                run_id=run.id,
                file_path="a.py",
                line_start=1,
                line_end=1,
                category=FindingCategory.CORRECTNESS,
                severity=Severity.HIGH,
                message=f"{name} secret finding",
                confidence=0.9,
                agent_name="diff_only",
            ),
            ContextBundleRecord(run_id=run.id, retrieval_strategy="test"),
            TokenUsageLedger(org_id=org.id, run_id=run.id, tokens_in=1, tokens_out=1, cost_usd=0),
            AuditLog(org_id=org.id, action="test", target=name),
        ]
    )
    provider = AIProvider(org_id=org.id, kind=ProviderKind.ANTHROPIC, encrypted_credentials="x")
    session.add(provider)
    session.flush()
    session.add(
        ModelRoute(org_id=org.id, tier=ModelTier.REVIEW, provider_id=provider.id, model_name="m")
    )
    session.flush()
    return Tenant(org_id=org.id, repo_id=repo.id, installation_github_id=installation_github_id)


@pytest.fixture
def two_tenants(db_engine: Engine) -> Iterator[tuple[Connection, Tenant, Tenant]]:
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    tenant_a = _seed_tenant(session, "Alpha", 1_000_001)
    tenant_b = _seed_tenant(session, "Bravo", 1_000_002)
    session.commit()  # releases the savepoint; the outer transaction still rolls back
    session.close()

    # From here on, this transaction acts exactly like the application.
    connection.execute(text(f"SET ROLE {APP_ROLE}"))
    yield connection, tenant_a, tenant_b

    transaction.rollback()
    connection.close()


def _act_for(connection: Connection, org_id: object | None) -> None:
    connection.execute(
        text("SELECT set_config('app.current_org', :org, true)"),
        {"org": str(org_id) if org_id else ""},
    )


def _expect_rejected(connection: Connection, sql: str, params: dict[str, object]) -> None:
    savepoint = connection.begin_nested()
    with pytest.raises(DBAPIError):
        connection.execute(text(sql), params)
    savepoint.rollback()


def test_the_application_role_cannot_bypass_row_level_security(
    two_tenants: tuple[Connection, Tenant, Tenant],
) -> None:
    connection, _, _ = two_tenants

    row = connection.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).one()

    assert (row.rolsuper, row.rolbypassrls) == (False, False)


def test_every_table_with_org_id_has_a_tenant_policy(db_engine: Engine) -> None:
    """Catalog check: a future table that carries `org_id` but ships without
    a policy fails here, not in production. `users` is the identity
    directory, read by login before any organisation is known."""
    with db_engine.connect() as connection:
        with_org_id = set(
            connection.scalars(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND column_name = 'org_id'"
                )
            )
        )
        rls_enabled = set(
            connection.scalars(
                text(
                    "SELECT relname FROM pg_class WHERE relrowsecurity "
                    "AND relnamespace = 'public'::regnamespace"
                )
            )
        )
        with_policy = set(
            connection.scalars(
                text("SELECT tablename FROM pg_policies WHERE policyname = 'tenant_isolation'")
            )
        )

    assert with_org_id - {"users"} == set(TENANT_TABLES)
    assert rls_enabled == set(TENANT_TABLES)
    assert with_policy == set(TENANT_TABLES)


def test_a_session_bound_to_no_organisation_sees_no_tenant_rows(
    two_tenants: tuple[Connection, Tenant, Tenant],
) -> None:
    connection, _, _ = two_tenants
    _act_for(connection, None)

    for table in TENANT_TABLES:
        count = connection.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
        assert count == 0, f"{table} leaked {count} rows to an unbound session"


def test_an_unfiltered_query_returns_only_the_bound_organisations_rows(
    two_tenants: tuple[Connection, Tenant, Tenant],
) -> None:
    connection, tenant_a, _ = two_tenants
    _act_for(connection, tenant_a.org_id)

    for table in TENANT_TABLES:
        org_ids = set(connection.scalars(text(f"SELECT org_id FROM {table}")))  # noqa: S608
        assert org_ids == {tenant_a.org_id}, f"{table}: {org_ids}"

    messages = list(connection.scalars(text("SELECT message FROM findings")))
    assert messages == ["Alpha secret finding"]


def test_rows_cannot_be_written_into_or_moved_to_another_organisation(
    two_tenants: tuple[Connection, Tenant, Tenant],
) -> None:
    connection, tenant_a, tenant_b = two_tenants
    _act_for(connection, tenant_a.org_id)

    _expect_rejected(
        connection,
        "INSERT INTO repositories (id, org_id, full_name, default_branch, languages, is_active, "
        "auto_review_enabled, config_json) VALUES (gen_random_uuid(), :org, 'x/y', 'main', "
        "'{}', true, false, '{}')",
        {"org": tenant_b.org_id},
    )
    _expect_rejected(
        connection,
        "UPDATE repositories SET org_id = :org WHERE id = :repo",
        {"org": tenant_b.org_id, "repo": tenant_a.repo_id},
    )


def test_another_organisations_rows_cannot_be_updated_or_deleted(
    two_tenants: tuple[Connection, Tenant, Tenant],
) -> None:
    connection, tenant_a, tenant_b = two_tenants
    _act_for(connection, tenant_a.org_id)

    updated = connection.execute(
        text("UPDATE repositories SET auto_review_enabled = true WHERE id = :repo"),
        {"repo": tenant_b.repo_id},
    )
    deleted = connection.execute(
        text("DELETE FROM findings WHERE org_id = :org"), {"org": tenant_b.org_id}
    )

    assert updated.rowcount == 0
    assert deleted.rowcount == 0
    _act_for(connection, tenant_b.org_id)
    assert connection.scalar(text("SELECT count(*) FROM findings")) == 1


def test_a_child_row_cannot_attach_to_another_organisations_parent(
    two_tenants: tuple[Connection, Tenant, Tenant],
) -> None:
    """Without `org_id`, the inherit trigger cannot see the other
    organisation's repository (NOT NULL fails). With its own `org_id`, the
    composite `(repo_id, org_id)` foreign key has no matching parent."""
    connection, tenant_a, tenant_b = two_tenants
    _act_for(connection, tenant_a.org_id)
    pr_columns = "id, repo_id, number, title, author, base_branch, head_sha, state, opened_at"
    pr_values = "gen_random_uuid(), :repo, 99, 't', 'a', 'main', 'abc1234', 'OPEN', now()"

    _expect_rejected(
        connection,
        f"INSERT INTO pull_requests ({pr_columns}) VALUES ({pr_values})",  # noqa: S608
        {"repo": tenant_b.repo_id},
    )
    _expect_rejected(
        connection,
        f"INSERT INTO pull_requests (org_id, {pr_columns}) VALUES (:org, {pr_values})",  # noqa: S608
        {"org": tenant_a.org_id, "repo": tenant_b.repo_id},
    )


def test_the_only_cross_organisation_lookup_maps_an_installation_to_its_org(
    two_tenants: tuple[Connection, Tenant, Tenant],
) -> None:
    """Webhooks need the organisation before they can bind to it. The
    SECURITY DEFINER function answers exactly that, while the installation
    rows themselves stay hidden."""
    connection, tenant_a, tenant_b = two_tenants
    _act_for(connection, tenant_a.org_id)

    resolved = connection.scalar(
        text("SELECT revu_installation_org(:id)"), {"id": tenant_b.installation_github_id}
    )
    visible = connection.scalar(
        text("SELECT count(*) FROM github_installations WHERE installation_id = :id"),
        {"id": tenant_b.installation_github_id},
    )

    assert resolved == tenant_b.org_id
    assert visible == 0


def test_the_application_role_cannot_touch_the_migration_table(
    two_tenants: tuple[Connection, Tenant, Tenant],
) -> None:
    connection, _, _ = two_tenants

    _expect_rejected(connection, "SELECT * FROM alembic_version", {})


async def test_startup_guard_flags_a_role_that_would_bypass_rls(db_engine: Engine) -> None:
    """The API and worker refuse to start in production when DATABASE_URL
    points at a superuser or BYPASSRLS role (e.g. the schema owner)."""
    from db.tenancy import rls_bypass_reason
    from db.testing import TEST_APP_DATABASE_URL, TEST_DATABASE_URL, async_url
    from sqlalchemy.ext.asyncio import create_async_engine

    owner = create_async_engine(async_url(TEST_DATABASE_URL))
    app = create_async_engine(async_url(TEST_APP_DATABASE_URL))
    try:
        assert "bypasses row-level security" in (await rls_bypass_reason(owner) or "")
        assert await rls_bypass_reason(app) is None
    finally:
        await owner.dispose()
        await app.dispose()
