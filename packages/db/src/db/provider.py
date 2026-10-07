import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db._enum import pg_enum
from db.base import Base
from db.mixins import CreatedAtMixin, TZDateTime, UUIDPrimaryKeyMixin


class ProviderKind(enum.StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GROQ = "groq"
    # Any server speaking the OpenAI chat-completions API: Ollama, vLLM,
    # LM Studio, llama.cpp, NVIDIA NIM, or a company's own fine-tuned model.
    OPENAI_COMPATIBLE = "openai_compatible"
    # Reserved for the managed-cloud integrations that come next.
    BEDROCK = "bedrock"
    AZURE = "azure"
    VERTEX = "vertex"


class ModelTier(enum.StrEnum):
    SCREEN = "screen"
    REVIEW = "review"
    VERIFY = "verify"


class AIProvider(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One organisation's connection to an LLM provider, configured from the
    AI Providers screen (never from environment variables).

    `encrypted_credentials` is AES-GCM ciphertext bound to this organisation
    and this row (`db.credentials`); the API never returns it or the key.
    `key_hint` is the only part of the key that is ever shown again.
    `settings` holds non-secret configuration: capability flags found by
    "Test connection" (`supports_tools`, `supports_json`), the context
    window, and per-million-token prices for models LiteLLM has no price
    table for (self-hosted and fine-tuned models).
    """

    __tablename__ = "ai_providers"
    __table_args__ = (
        UniqueConstraint("id", "org_id", name="uq_ai_providers_id_org"),
        UniqueConstraint("org_id", "name", name="uq_ai_providers_org_name"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[ProviderKind] = mapped_column(pg_enum(ProviderKind), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500))
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    key_hint: Mapped[str | None] = mapped_column(String(32))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # Last "Test connection": when it last succeeded, and the error if the
    # most recent attempt failed (None once a test passes again).
    verified_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_test_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    # Deleting a provider removes the routes that use it; the database's
    # ON DELETE CASCADE does it, rather than the ORM nulling foreign keys.
    model_routes: Mapped[list["ModelRoute"]] = relationship(
        back_populates="provider", cascade="all, delete-orphan", passive_deletes=True
    )


class ModelRoute(UUIDPrimaryKeyMixin, Base):
    """Which provider + model handles each review step for an organisation:
    `screen` (cheap triage), `review` (the reviewer itself) and `verify`."""

    __tablename__ = "model_routes"
    __table_args__ = (
        UniqueConstraint("org_id", "tier", name="uq_model_routes_org_tier"),
        ForeignKeyConstraint(
            ["provider_id", "org_id"],
            ["ai_providers.id", "ai_providers.org_id"],
            ondelete="CASCADE",
            name="fk_model_routes_provider_org",
        ),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    tier: Mapped[ModelTier] = mapped_column(pg_enum(ModelTier), nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    provider: Mapped[AIProvider] = relationship(back_populates="model_routes")
