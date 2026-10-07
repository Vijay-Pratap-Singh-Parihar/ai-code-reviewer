"""AI providers configured from the UI: names, endpoints, encrypted keys, settings

Revision ID: e8b2d4f6a1c3
Revises: d7a9c1e3f5b2
Create Date: 2026-10-08

Turns the placeholder `ai_providers` / `model_routes` tables (Stage 1) into
the real AI Providers feature:

- `providerkind` gains `groq` and `openai_compatible` (Ollama, vLLM,
  LM Studio, NVIDIA NIM, or a company's own fine-tuned model behind an
  OpenAI-compatible endpoint).
- Providers get a display `name` (unique per organisation), an optional
  `base_url`, a `key_hint` (the only part of a key ever shown again),
  non-secret `settings`, test results and who/when metadata.
- `is_default` goes away: model routes (screen / review / verify) decide
  which provider each step uses.

Credentials stay in `encrypted_credentials`, now AES-GCM bound to the
organisation and provider (`db.credentials`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e8b2d4f6a1c3"
down_revision: str | None = "d7a9c1e3f5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A new enum value can't be used in the transaction that adds it, and
    # nothing below uses these, but adding them outside the migration's
    # transaction keeps that true even if this file grows later.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE providerkind ADD VALUE IF NOT EXISTS 'groq'")
        op.execute("ALTER TYPE providerkind ADD VALUE IF NOT EXISTS 'openai_compatible'")

    op.add_column("ai_providers", sa.Column("name", sa.String(100)))
    op.execute("UPDATE ai_providers SET name = kind::text || '-' || left(id::text, 8)")
    op.alter_column("ai_providers", "name", nullable=False)
    op.create_unique_constraint("uq_ai_providers_org_name", "ai_providers", ["org_id", "name"])
    op.add_column("ai_providers", sa.Column("base_url", sa.String(500)))
    op.add_column("ai_providers", sa.Column("key_hint", sa.String(32)))
    op.add_column(
        "ai_providers",
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.alter_column("ai_providers", "settings", server_default=None)
    op.add_column("ai_providers", sa.Column("last_test_error", sa.Text()))
    op.add_column(
        "ai_providers",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.add_column("ai_providers", sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.add_column(
        "ai_providers",
        sa.Column(
            "created_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.drop_column("ai_providers", "is_default")
    op.add_column("model_routes", sa.Column("updated_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("model_routes", "updated_at")
    op.add_column(
        "ai_providers",
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.drop_column("ai_providers", "created_by_user_id")
    op.drop_column("ai_providers", "updated_at")
    op.drop_column("ai_providers", "created_at")
    op.drop_column("ai_providers", "last_test_error")
    op.drop_column("ai_providers", "settings")
    op.drop_column("ai_providers", "key_hint")
    op.drop_column("ai_providers", "base_url")
    op.drop_constraint("uq_ai_providers_org_name", "ai_providers", type_="unique")
    op.drop_column("ai_providers", "name")
    # Postgres can't drop enum values; 'groq' and 'openai_compatible' stay
    # in the type, unused, which is harmless.
