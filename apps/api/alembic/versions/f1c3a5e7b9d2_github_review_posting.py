"""posting runs to GitHub as pull request reviews

Revision ID: f1c3a5e7b9d2
Revises: e8b2d4f6a1c3
Create Date: 2026-10-30

A run records the GitHub review it was posted as (id, URL, when), which is
what makes posting idempotent, and why the last attempt failed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1c3a5e7b9d2"
down_revision: str | None = "e8b2d4f6a1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("analysis_runs", sa.Column("github_review_id", sa.BigInteger()))
    op.add_column("analysis_runs", sa.Column("github_review_url", sa.String(500)))
    op.add_column("analysis_runs", sa.Column("posted_at", sa.DateTime(timezone=True)))
    op.add_column("analysis_runs", sa.Column("post_error", sa.Text()))


def downgrade() -> None:
    op.drop_column("analysis_runs", "post_error")
    op.drop_column("analysis_runs", "posted_at")
    op.drop_column("analysis_runs", "github_review_url")
    op.drop_column("analysis_runs", "github_review_id")
