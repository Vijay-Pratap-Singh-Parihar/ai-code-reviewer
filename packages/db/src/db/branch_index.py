import enum
import uuid
from datetime import datetime

from sqlalchemy import ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db._enum import pg_enum
from db.base import Base
from db.mixins import CreatedAtMixin, TZDateTime, UUIDPrimaryKeyMixin, inherited_org_id


class BranchIndexStatus(enum.StrEnum):
    PENDING = "pending"
    BUILDING = "building"
    READY = "ready"
    STALE = "stale"
    FAILED = "failed"


class IndexUpdateMode(enum.StrEnum):
    INCREMENTAL = "incremental"
    FULL = "full"


class BranchIndex(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One index snapshot *attempt* per (repository, base_branch), versioned by
    head_sha and ordered by `created_at`.

    **Stage 5 design decision:** rather than mutating a single row per
    (repo_id, branch_name) in place across rebuilds, each build attempt gets
    its own row. This is what makes "index staleness is a first-class state"
    (Product_Architecture_FullStack.md §2) trivial to get right: a row is
    only ever written to `status=ready` once every column on it reflects a
    complete, successful build, and older `ready` rows for the same branch
    are left untouched (not deleted, not overwritten) until a newer build
    also reaches `ready`. "The current index for this branch" is therefore
    always `SELECT ... WHERE status = 'ready' ORDER BY created_at DESC
    LIMIT 1` (see `api.services.branch_index.get_current_branch_index`) — a
    reader can never observe a half-written graph, because a row that isn't
    fully written is never `ready` in the first place, and a `building`/
    `pending`/`failed` row for the same branch is simply invisible to that
    query. `head_sha` is nullable because a row starts life as `pending`
    before the target commit has even been resolved (that resolution needs a
    real git checkout, which is worker-side work, not something the API
    layer that creates the row does).
    """

    __tablename__ = "branch_index"
    __table_args__ = (
        Index("ix_branch_index_repo_branch_created", "repo_id", "branch_name", "created_at"),
        UniqueConstraint("id", "org_id", name="uq_branch_index_id_org"),
        ForeignKeyConstraint(
            ["repo_id", "org_id"],
            ["repositories.id", "repositories.org_id"],
            ondelete="CASCADE",
            name="fk_branch_index_repo_org",
        ),
    )

    org_id: Mapped[uuid.UUID] = inherited_org_id()
    repo_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    branch_name: Mapped[str] = mapped_column(String(255), nullable=False)
    head_sha: Mapped[str | None] = mapped_column(String(40))
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


class IndexUpdateLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Audit trail of every rebuild/incremental-update attempt on a branch index.

    `mode` and `reason` are what let you report rebuild frequency as an
    operational cost, per the architecture doc's note on force-push handling.
    """

    __tablename__ = "index_update_log"
    __table_args__ = (
        ForeignKeyConstraint(
            ["branch_index_id", "org_id"],
            ["branch_index.id", "branch_index.org_id"],
            ondelete="CASCADE",
            name="fk_index_update_log_branch_index_org",
        ),
    )

    org_id: Mapped[uuid.UUID] = inherited_org_id()
    branch_index_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_sha: Mapped[str | None] = mapped_column(String(40))
    to_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    files_changed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mode: Mapped[IndexUpdateMode] = mapped_column(pg_enum(IndexUpdateMode), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))

    branch_index: Mapped[BranchIndex] = relationship(back_populates="update_log")
