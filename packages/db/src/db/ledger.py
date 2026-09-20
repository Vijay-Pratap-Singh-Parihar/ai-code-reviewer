import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from db.mixins import TZDateTime, UUIDPrimaryKeyMixin


class TokenUsageLedger(UUIDPrimaryKeyMixin, Base):
    """Append-only record of actual token/cost consumption per run, normalised
    across providers. This is what the Usage and Budget screen sums, not the
    pre-flight estimate.
    """

    __tablename__ = "token_usage_ledger"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime, server_default=func.now())


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_log"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    extra: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )
    at: Mapped[datetime] = mapped_column(TZDateTime, server_default=func.now())
