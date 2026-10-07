import uuid
from datetime import datetime

from db.branch_index import BranchIndexStatus, IndexUpdateMode
from pydantic import BaseModel, Field


class IndexUpdateLogPublic(BaseModel):
    mode: IndexUpdateMode
    from_sha: str | None
    to_sha: str
    files_changed: int
    duration_ms: int
    reason: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class BranchIndexPublic(BaseModel):
    """One `BranchIndex` row (one build attempt) as returned right after
    triggering a build — `status` will be `pending`, not yet `ready`.
    """

    id: uuid.UUID
    repo_id: uuid.UUID
    branch_name: str
    head_sha: str | None
    status: BranchIndexStatus
    node_count: int
    edge_count: int
    build_duration_ms: int | None
    built_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class BranchIndexStatusPublic(BaseModel):
    """The "Branch Memory" screen's data: the current *usable* snapshot (if
    any), plus whether it's stale because a newer build is in flight or the
    latest attempt failed. Never reflects a `building` row's half-written
    columns — see `api.services.branch_index.get_current_branch_index` for
    why that's structurally impossible, not just avoided by convention.
    """

    repo_id: uuid.UUID
    branch_name: str
    has_ready_index: bool
    head_sha: str | None
    node_count: int
    edge_count: int
    unresolved_count: int
    build_duration_ms: int | None
    built_at: datetime | None
    ready_index_id: uuid.UUID | None
    is_stale: bool
    latest_attempt_status: BranchIndexStatus | None
    latest_attempt_id: uuid.UUID | None
    last_update: IndexUpdateLogPublic | None
    recent_updates: list[IndexUpdateLogPublic] = Field(default_factory=list)
