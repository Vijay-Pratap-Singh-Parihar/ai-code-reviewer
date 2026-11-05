"""monthly budget: spend and token caps per organisation, and a usage ledger
that is actually written

Revision ID: c9e1a3b5d7f2
Revises: b3e5a7c9d1f4
Create Date: 2026-11-05

* `organizations.monthly_budget_usd` (NULL = no spend cap) and
  `budget_warn_percent` (when the app starts warning). The existing
  `token_quota_monthly` becomes the token cap (0 = none), which matters for
  local models that report no cost.
* `token_used_current_period` is dropped: nothing kept it up to date, and
  the month's usage is now summed from the ledger.
* `token_usage_ledger` gets one row per run at most (a redelivered job
  can't count twice) and an index for "this organisation, this month".
  Runs that already finished are copied in, so this month's figures
  include them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9e1a3b5d7f2"
down_revision: str | None = "b3e5a7c9d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("monthly_budget_usd", sa.Numeric(12, 2)))
    op.add_column(
        "organizations",
        sa.Column("budget_warn_percent", sa.SmallInteger(), server_default="80", nullable=False),
    )
    op.drop_column("organizations", "token_used_current_period")

    op.create_unique_constraint("uq_token_usage_ledger_run", "token_usage_ledger", ["run_id"])
    op.create_index(
        "ix_token_usage_ledger_org_occurred", "token_usage_ledger", ["org_id", "occurred_at"]
    )
    op.execute(
        """
        INSERT INTO token_usage_ledger (id, org_id, run_id, tokens_in, tokens_out, cost_usd, occurred_at)
        SELECT gen_random_uuid(), org_id, id, tokens_in, tokens_out, cost_usd,
               COALESCE(finished_at, created_at)
        FROM analysis_runs
        WHERE status = 'succeeded'
        ON CONFLICT (run_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_token_usage_ledger_org_occurred", "token_usage_ledger")
    op.drop_constraint("uq_token_usage_ledger_run", "token_usage_ledger", type_="unique")
    op.add_column(
        "organizations",
        sa.Column("token_used_current_period", sa.Integer(), server_default="0", nullable=False),
    )
    op.drop_column("organizations", "budget_warn_percent")
    op.drop_column("organizations", "monthly_budget_usd")
