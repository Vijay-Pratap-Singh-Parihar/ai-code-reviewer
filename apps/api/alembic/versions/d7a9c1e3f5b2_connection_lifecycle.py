"""connection lifecycle: suspension/uninstall state, disconnected repos, platform admins

Revision ID: d7a9c1e3f5b2
Revises: c3f1e2d4b5a6
Create Date: 2026-10-08

- `github_installations.suspended_at` / `uninstalled_at`: an uninstalled
  installation is kept (not deleted) so its repositories stay grouped under
  it until their data is purged.
- `repositories.disconnected_at`: starts the retention clock when a
  repository stops being reachable; the retention job purges it afterwards.
- `users.is_platform_admin`: the deployment-wide operator allowed to create
  the GitHub App, which no organisation owner can do any more.
- `ix_audit_log_org_at`: the audit screen lists an organisation's entries
  newest first.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7a9c1e3f5b2"
down_revision: str | None = "c3f1e2d4b5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "github_installations", sa.Column("suspended_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "github_installations", sa.Column("uninstalled_at", sa.DateTime(timezone=True))
    )
    op.add_column("repositories", sa.Column("disconnected_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_repositories_disconnected_at",
        "repositories",
        ["disconnected_at"],
        postgresql_where=sa.text("disconnected_at IS NOT NULL"),
    )
    # Repositories that were already inactive (removed before this column
    # existed) start their retention period now rather than never.
    op.execute(
        "UPDATE repositories SET disconnected_at = now() "
        "WHERE is_active = false AND installation_id IS NOT NULL"
    )
    op.add_column(
        "users",
        sa.Column(
            "is_platform_admin", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.create_index("ix_audit_log_org_at", "audit_log", ["org_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_org_at", table_name="audit_log")
    op.drop_column("users", "is_platform_admin")
    op.drop_index("ix_repositories_disconnected_at", table_name="repositories")
    op.drop_column("repositories", "disconnected_at")
    op.drop_column("github_installations", "uninstalled_at")
    op.drop_column("github_installations", "suspended_at")
