"""users' first and last names

Revision ID: b3e5a7c9d1f4
Revises: a2d4f6b8c1e3
Create Date: 2026-10-30

Shown in the app instead of the email. Nullable: accounts created before
this have no name until they add one from their profile.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3e5a7c9d1f4"
down_revision: str | None = "a2d4f6b8c1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("first_name", sa.String(100)))
    op.add_column("users", sa.Column("last_name", sa.String(100)))


def downgrade() -> None:
    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")
