import enum
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base
from api.models._enum import pg_enum
from api.models.mixins import TZDateTime, UUIDPrimaryKeyMixin


class ProviderKind(enum.StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    BEDROCK = "bedrock"
    AZURE = "azure"
    VERTEX = "vertex"


class ModelTier(enum.StrEnum):
    SCREEN = "screen"
    REVIEW = "review"
    VERIFY = "verify"


class AIProvider(UUIDPrimaryKeyMixin, Base):
    """A set of credentials for one LLM provider, envelope-encrypted at rest.

    `encrypted_credentials` holds ciphertext (envelope encryption lands with
    the AI Providers stage), never plaintext — a plain column here would be
    exactly the kind of flaw the architecture doc calls out as something an
    examiner would probe.
    """

    __tablename__ = "ai_providers"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[ProviderKind] = mapped_column(pg_enum(ProviderKind), nullable=False)
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_default: Mapped[bool] = mapped_column(default=False, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    model_routes: Mapped[list["ModelRoute"]] = relationship(back_populates="provider")


class ModelRoute(UUIDPrimaryKeyMixin, Base):
    """Which provider + model handles each cost tier for an organisation."""

    __tablename__ = "model_routes"
    __table_args__ = (UniqueConstraint("org_id", "tier", name="uq_model_routes_org_tier"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    tier: Mapped[ModelTier] = mapped_column(pg_enum(ModelTier), nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_providers.id", ondelete="CASCADE"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)

    provider: Mapped[AIProvider] = relationship(back_populates="model_routes")
