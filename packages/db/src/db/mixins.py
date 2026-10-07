import uuid
from datetime import datetime

from sqlalchemy import DateTime, FetchedValue, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

# Application code deals exclusively in timezone-aware UTC datetimes
# (`datetime.now(UTC)`). Every timestamp column must be TIMESTAMPTZ, or a
# naive column silently disagrees with that and breaks comparisons/writes.
TZDateTime = DateTime(timezone=True)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


def inherited_org_id() -> Mapped[uuid.UUID]:
    """`org_id` on a table whose rows belong to a parent row (a PR to its
    repository, a finding to its run, ...). Row-level security filters on it.

    It is part of a composite foreign key `(parent_id, org_id) -> parent(id,
    org_id)`, so a child can never belong to a different organisation than
    its parent. When an insert omits it, the `revu_inherit_org_id` trigger
    copies it from the parent (looked up under RLS, so a parent from another
    organisation is invisible and the insert fails); `FetchedValue` tells
    SQLAlchemy to leave it out of the INSERT and read it back afterwards.
    """
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        server_default=FetchedValue(),
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(TZDateTime, server_default=func.now())


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, server_default=func.now(), onupdate=func.now()
    )
