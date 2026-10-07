import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AuditEntryPublic(BaseModel):
    id: uuid.UUID
    at: datetime
    action: str
    target: str
    actor_id: uuid.UUID | None
    actor_email: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditPage(BaseModel):
    entries: list[AuditEntryPublic]
    # Pass as `before` to fetch the next (older) page; None on the last page.
    next_before: datetime | None = None
