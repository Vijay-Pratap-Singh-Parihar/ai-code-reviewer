"""finding feedback: why a finding was dismissed, and who gave the feedback

Revision ID: d4f6a8c0e2b7
Revises: c9e1a3b5d7f2
Create Date: 2026-11-05

`findings.developer_action` (accepted / dismissed) and `resolved_at` (when)
already existed but nothing set them. Feedback now comes from the run page;
this adds the reason a finding was dismissed (incorrect, not important,
already handled, other), which separates false positives from correct but
low-value findings, and who gave it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "d4f6a8c0e2b7"
down_revision: str | None = "c9e1a3b5d7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("findings", sa.Column("feedback_reason", sa.String(32)))
    op.add_column(
        "findings",
        sa.Column(
            "feedback_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="findings_feedback_by_fkey"),
        ),
    )


def downgrade() -> None:
    op.drop_column("findings", "feedback_by")
    op.drop_column("findings", "feedback_reason")
