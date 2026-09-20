import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
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


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(TZDateTime, server_default=func.now())


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, server_default=func.now(), onupdate=func.now()
    )
