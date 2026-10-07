"""tenant isolation: org_id on every tenant table, composite keys, row-level security

Revision ID: c3f1e2d4b5a6
Revises: a0b68475a72c
Create Date: 2026-10-07

Makes the database itself enforce that one organisation can never read or
write another's rows (see `db.tenancy` for how the application binds a
session to an organisation):

1. Repositories become unique per organisation (`org_id, full_name` and
   `org_id, github_repo_id`) so two organisations can connect the same
   GitHub repository as fully separate copies.
2. Every child table gets `org_id`, backfilled from its parent, and its
   parent foreign key becomes composite `(parent_id, org_id)`, so a child
   can never belong to a different organisation than its parent.
3. A `BEFORE INSERT` trigger fills a child's `org_id` from its parent when
   the insert leaves it out.
4. Row-level security on every tenant table, keyed on the transaction-local
   setting `app.current_org` (`revu_current_org()`).
5. `revu_app`, the role the API and worker connect as: not a superuser, no
   BYPASSRLS, only DML on the application tables. Migrations keep running
   as the schema owner, which RLS deliberately does not apply to.
6. `revu_installation_org(bigint)`, the one SECURITY DEFINER lookup: maps a
   GitHub installation id to its organisation, for webhook deliveries (which
   arrive before any organisation is known) and for the "already linked to
   another organisation" check.

The SQL here is written out literally rather than generated from
`db.tenancy`, so this migration keeps doing the same thing if that module
changes later.
"""

import os
from collections.abc import Sequence

from alembic import op

revision: str = "c3f1e2d4b5a6"
down_revision: str | None = "a0b68475a72c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "revu_app"

# (table, parent table, column referencing the parent, old single-column FK).
# Parents come before their children: each table is backfilled from a parent
# whose org_id is already filled in.
CHILDREN = (
    ("tracked_branches", "repositories", "repo_id", "tracked_branches_repo_id_fkey"),
    ("pull_requests", "repositories", "repo_id", "pull_requests_repo_id_fkey"),
    ("branch_index", "repositories", "repo_id", "branch_index_repo_id_fkey"),
    ("analysis_runs", "pull_requests", "pr_id", "analysis_runs_pr_id_fkey"),
    ("findings", "analysis_runs", "run_id", "findings_run_id_fkey"),
    ("context_bundles", "analysis_runs", "run_id", "context_bundles_run_id_fkey"),
    (
        "index_update_log",
        "branch_index",
        "branch_index_id",
        "index_update_log_branch_index_id_fkey",
    ),
)

# Constraint names, matching the models.
COMPOSITE_FK_NAMES = {
    "tracked_branches": "fk_tracked_branches_repo_org",
    "pull_requests": "fk_pull_requests_repo_org",
    "branch_index": "fk_branch_index_repo_org",
    "analysis_runs": "fk_analysis_runs_pr_org",
    "findings": "fk_findings_run_org",
    "context_bundles": "fk_context_bundles_run_org",
    "index_update_log": "fk_index_update_log_branch_index_org",
}
UNIQUE_ID_ORG_NAMES = {
    "repositories": "uq_repositories_id_org",
    "pull_requests": "uq_pull_requests_id_org",
    "analysis_runs": "uq_analysis_runs_id_org",
    "branch_index": "uq_branch_index_id_org",
    "ai_providers": "uq_ai_providers_id_org",
}

TENANT_TABLES = (
    "repositories",
    "tracked_branches",
    "pull_requests",
    "analysis_runs",
    "findings",
    "context_bundles",
    "branch_index",
    "index_update_log",
    "github_installations",
    "ai_providers",
    "model_routes",
    "token_usage_ledger",
    "audit_log",
)


def _app_role_password() -> str:
    # Only used the first time the role is created. Production provisions
    # the role (and its secret) out of band; this default is for local dev
    # and CI, matching docker-compose and .env.example.
    password = os.environ.get("REVU_APP_DB_PASSWORD", "revu_app_dev_password")
    return password.replace("'", "''")


def upgrade() -> None:
    # 1. Repositories: unique per organisation, and a composite-key target.
    op.drop_constraint("repositories_full_name_key", "repositories", type_="unique")
    op.drop_constraint("repositories_github_repo_id_key", "repositories", type_="unique")
    op.create_unique_constraint(
        "uq_repositories_org_full_name", "repositories", ["org_id", "full_name"]
    )
    op.create_unique_constraint(
        "uq_repositories_org_github_repo_id", "repositories", ["org_id", "github_repo_id"]
    )
    op.create_unique_constraint(
        UNIQUE_ID_ORG_NAMES["repositories"], "repositories", ["id", "org_id"]
    )
    op.create_unique_constraint(
        UNIQUE_ID_ORG_NAMES["ai_providers"], "ai_providers", ["id", "org_id"]
    )

    # 2. org_id on every child table, backfilled, then composite foreign keys.
    for table, parent, fk_col, old_fk in CHILDREN:
        op.execute(f"ALTER TABLE {table} ADD COLUMN org_id uuid")
        op.execute(
            f"UPDATE {table} AS c SET org_id = p.org_id FROM {parent} AS p WHERE p.id = c.{fk_col}"
        )
        op.execute(f"ALTER TABLE {table} ALTER COLUMN org_id SET NOT NULL")
        op.create_foreign_key(
            f"{table}_org_id_fkey", table, "organizations", ["org_id"], ["id"], ondelete="CASCADE"
        )
        op.create_index(f"ix_{table}_org_id", table, ["org_id"])
        if table in UNIQUE_ID_ORG_NAMES:
            op.create_unique_constraint(UNIQUE_ID_ORG_NAMES[table], table, ["id", "org_id"])
        op.drop_constraint(old_fk, table, type_="foreignkey")
        op.create_foreign_key(
            COMPOSITE_FK_NAMES[table],
            table,
            parent,
            [fk_col, "org_id"],
            ["id", "org_id"],
            ondelete="CASCADE",
        )

    op.drop_constraint("token_usage_ledger_run_id_fkey", "token_usage_ledger", type_="foreignkey")
    op.create_foreign_key(
        "fk_token_usage_ledger_run_org",
        "token_usage_ledger",
        "analysis_runs",
        ["run_id", "org_id"],
        ["id", "org_id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("model_routes_provider_id_fkey", "model_routes", type_="foreignkey")
    op.create_foreign_key(
        "fk_model_routes_provider_org",
        "model_routes",
        "ai_providers",
        ["provider_id", "org_id"],
        ["id", "org_id"],
        ondelete="CASCADE",
    )

    # 3. Children inherit org_id from their parent when an insert omits it.
    # Runs as the inserting role, so the parent lookup is itself under RLS:
    # another organisation's parent is invisible, org_id stays NULL, and the
    # NOT NULL constraint rejects the row.
    op.execute(
        """
        CREATE FUNCTION revu_inherit_org_id() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.org_id IS NULL THEN
                EXECUTE format('SELECT org_id FROM %I WHERE id = $1', TG_ARGV[0])
                    INTO NEW.org_id
                    USING (to_jsonb(NEW) ->> TG_ARGV[1])::uuid;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    for table, parent, fk_col, _old_fk in CHILDREN:
        op.execute(
            f"CREATE TRIGGER trg_{table}_inherit_org_id BEFORE INSERT ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION revu_inherit_org_id('{parent}', '{fk_col}')"
        )

    # 4. Row-level security. An unset or empty app.current_org yields NULL,
    # which matches no row: unbound sessions see nothing (deny by default).
    op.execute(
        """
        CREATE FUNCTION revu_current_org() RETURNS uuid
        LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.current_org', true), '')::uuid $$
        """
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (org_id = revu_current_org()) WITH CHECK (org_id = revu_current_org())"
        )

    # 5. The application role.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{_app_role_password()}'
                    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
            END IF;
            EXECUTE format('GRANT CONNECT ON DATABASE %I TO {APP_ROLE}', current_database());
        END
        $$
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"REVOKE ALL ON alembic_version FROM {APP_ROLE}")
    # Tables later migrations create get the same grants; whether they need
    # a policy is enforced by the tenant-isolation catalog test.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}"
    )

    # 6. The single cross-organisation lookup, owned by the schema owner.
    op.execute(
        """
        CREATE FUNCTION revu_installation_org(p_installation_id bigint) RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
        $$ SELECT org_id FROM github_installations WHERE installation_id = p_installation_id $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION revu_installation_org(bigint) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION revu_installation_org(bigint) TO {APP_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION revu_current_org() TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION revu_installation_org(bigint)")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM {APP_ROLE}"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE}"
    )
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
    # The role itself is cluster-wide (other databases, e.g. the test one,
    # may still use it), so it is left in place.

    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION revu_current_org()")

    for table, _parent, _fk_col, _old_fk in CHILDREN:
        op.execute(f"DROP TRIGGER trg_{table}_inherit_org_id ON {table}")
    op.execute("DROP FUNCTION revu_inherit_org_id()")

    op.drop_constraint("fk_model_routes_provider_org", "model_routes", type_="foreignkey")
    op.create_foreign_key(
        "model_routes_provider_id_fkey",
        "model_routes",
        "ai_providers",
        ["provider_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("fk_token_usage_ledger_run_org", "token_usage_ledger", type_="foreignkey")
    op.create_foreign_key(
        "token_usage_ledger_run_id_fkey",
        "token_usage_ledger",
        "analysis_runs",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )

    for table, parent, fk_col, old_fk in reversed(CHILDREN):
        op.drop_constraint(COMPOSITE_FK_NAMES[table], table, type_="foreignkey")
        op.create_foreign_key(old_fk, table, parent, [fk_col], ["id"], ondelete="CASCADE")
        if table in UNIQUE_ID_ORG_NAMES:
            op.drop_constraint(UNIQUE_ID_ORG_NAMES[table], table, type_="unique")
        op.drop_index(f"ix_{table}_org_id", table_name=table)
        op.drop_column(table, "org_id")

    op.drop_constraint(UNIQUE_ID_ORG_NAMES["ai_providers"], "ai_providers", type_="unique")
    op.drop_constraint(UNIQUE_ID_ORG_NAMES["repositories"], "repositories", type_="unique")
    op.drop_constraint("uq_repositories_org_github_repo_id", "repositories", type_="unique")
    op.drop_constraint("uq_repositories_org_full_name", "repositories", type_="unique")
    # Fails if two organisations now hold the same repository, which is the
    # point: going back to global uniqueness needs a human decision first.
    op.create_unique_constraint(
        "repositories_github_repo_id_key", "repositories", ["github_repo_id"]
    )
    op.create_unique_constraint("repositories_full_name_key", "repositories", ["full_name"])
