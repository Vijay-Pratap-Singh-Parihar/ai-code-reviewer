import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base
from api.models.mixins import CreatedAtMixin, TZDateTime, UUIDPrimaryKeyMixin


class RefreshToken(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One issued refresh token, identified by the hash of its raw value.

    Not part of Product_Architecture_FullStack.md's table list — that doc
    specifies "rotating refresh (httpOnly cookie)" as a requirement without
    naming its storage, so this table is Stage 2's addition to implement it.
    Rotation: on use, the row is marked `revoked_at` and `replaced_by_id`
    points at its successor. If a revoked token is presented again, that's
    reuse of a stolen token — the caller should revoke the whole chain.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("refresh_tokens.id", ondelete="SET NULL")
    )
