"""how much each review step's model may think (reasoning effort)

Revision ID: a2d4f6b8c1e3
Revises: f1c3a5e7b9d2
Create Date: 2026-10-30

`NULL` leaves it to the model's own default; otherwise one of off, low,
medium or high, chosen per review step on the AI Providers screen.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2d4f6b8c1e3"
down_revision: str | None = "f1c3a5e7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("model_routes", sa.Column("reasoning", sa.String(16)))
    op.create_check_constraint(
        "ck_model_routes_reasoning",
        "model_routes",
        "reasoning IN ('off', 'low', 'medium', 'high')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_model_routes_reasoning", "model_routes", type_="check")
    op.drop_column("model_routes", "reasoning")
