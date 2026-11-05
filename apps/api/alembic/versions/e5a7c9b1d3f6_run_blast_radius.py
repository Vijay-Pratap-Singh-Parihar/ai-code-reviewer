"""a run's blast radius: the code its changes can affect

Revision ID: e5a7c9b1d3f6
Revises: d4f6a8c0e2b7
Create Date: 2026-11-05

Computed by the worker from the branch's code graph for cross-file
reviews (`revu.context.impact.blast_radius`) and shown on the run page.
NULL for runs without a graph.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e5a7c9b1d3f6"
down_revision: str | None = "d4f6a8c0e2b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("analysis_runs", sa.Column("blast_radius", JSONB()))


def downgrade() -> None:
    op.drop_column("analysis_runs", "blast_radius")
