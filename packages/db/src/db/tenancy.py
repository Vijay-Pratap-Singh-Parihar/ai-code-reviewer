"""Database-enforced tenant isolation: which organisation a session acts for.

Every tenant table carries `org_id` and has a Postgres row-level security
policy (created by the `tenant_isolation_rls` migration):

    USING (org_id = revu_current_org()) WITH CHECK (org_id = revu_current_org())

`revu_current_org()` reads the transaction-local setting `app.current_org`.
The application connects as `revu_app`, a role that is not a superuser and
cannot bypass RLS, so a query that forgets its `WHERE org_id = ...` still
only ever sees, inserts or updates the current organisation's rows. A
session that was never bound to an organisation sees no tenant rows at all.

Binding is per *session* (`session.info`) and is re-applied at the start of
every transaction by an `after_begin` listener, because `set_config(...,
true)` lasts only until the transaction ends and our sessions commit
several times. The setting is always written, empty when unbound, so a
pooled connection can never carry a previous request's organisation into
the next one.

Tables deliberately *without* RLS: `organizations`, `users` and
`refresh_tokens` (the identity directory, read by login before any
organisation is known), and the deployment-wide `github_app_credentials`
and `webhook_deliveries`.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import Session

APP_ROLE = "revu_app"
ORG_SETTING = "app.current_org"
_INFO_KEY = "revu_org_id"

# Every table with a row-level security policy. A catalog test asserts that
# this list matches the tables that actually carry `org_id` (minus `users`),
# so a new tenant table cannot ship without a policy.
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

_SET_ORG = text(f"SELECT set_config('{ORG_SETTING}', :org, true)")


def _org_param(org_id: uuid.UUID | None) -> dict[str, str]:
    return {"org": str(org_id) if org_id else ""}


@event.listens_for(Session, "after_begin")
def _apply_org_on_begin(session: Session, transaction: Any, connection: Any) -> None:
    connection.execute(_SET_ORG, _org_param(session.info.get(_INFO_KEY)))


async def bind_org(session: AsyncSession, org_id: uuid.UUID | None) -> None:
    """Make every following statement on `session` act for `org_id` (or for
    no organisation, when `None`), including in the transaction already open."""
    session.info[_INFO_KEY] = org_id
    if session.in_transaction():
        await session.execute(_SET_ORG, _org_param(org_id))


def bound_org(session: AsyncSession) -> uuid.UUID | None:
    org: uuid.UUID | None = session.info.get(_INFO_KEY)
    return org


async def rls_bypass_reason(engine: AsyncEngine) -> str | None:
    """Why row-level security would *not* apply to connections from
    `engine`, or None when it does. Superusers and BYPASSRLS roles skip every
    policy silently, so pointing `DATABASE_URL` at the schema owner would
    quietly turn tenant isolation back into "only as good as each WHERE
    clause". Services check this at startup."""
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT current_user AS name, rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
    if row.rolsuper or row.rolbypassrls:
        return (
            f"connected to Postgres as '{row.name}', which bypasses row-level security; "
            f"point DATABASE_URL at the '{APP_ROLE}' role"
        )
    return None
