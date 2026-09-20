import enum
import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base
from api.models._enum import pg_enum
from api.models.mixins import TZDateTime, UUIDPrimaryKeyMixin


class BranchIndexStatus(enum.StrEnum):
    PENDING = "pending"
    BUILDING = "building"
    READY = "ready"
    STALE = "stale"
    FAILED = "failed"


class IndexUpdateMode(enum.StrEnum):
    INCREMENTAL = "incremental"
    FULL = "full"


class BranchIndex(UUIDPrimaryKeyMixin, Base):
    """One index snapshot per (repository, base_branch), versioned by head_sha.

    See Product_Architecture_FullStack.md §2 for the branch-memory design this
    table implements, including why `status` must model staleness explicitly
    rather than assuming the latest row is always current.
    """

    __tablename__ = "branch_index"

    repo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    branch_name: Mapped[str] = mapped_column(String(255), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    node_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    edge_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    graph_ref: Mapped[str | None] = mapped_column(String(512))
    unresolved_symbols: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    status: Mapped[BranchIndexStatus] = mapped_column(
        pg_enum(BranchIndexStatus), default=BranchIndexStatus.PENDING, nullable=False
    )
    built_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    updated_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    build_duration_ms: Mapped[int | None] = mapped_column(Integer)

    update_log: Mapped[list["IndexUpdateLog"]] = relationship(
        back_populates="branch_index", cascade="all, delete-orphan"
    )


class IndexUpdateLog(UUIDPrimaryKeyMixin, Base):
    """Audit trail of every rebuild/incremental-update attempt on a branch index.

    `mode` and `reason` are what let you report rebuild frequency as an
    operational cost, per the architecture doc's note on force-push handling.
    """

    __tablename__ = "index_update_log"

    branch_index_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("branch_index.id", ondelete="CASCADE"), nullable=False
    )
    from_sha: Mapped[str | None] = mapped_column(String(40))
    to_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    files_changed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mode: Mapped[IndexUpdateMode] = mapped_column(pg_enum(IndexUpdateMode), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))

    branch_index: Mapped[BranchIndex] = relationship(back_populates="update_log")
