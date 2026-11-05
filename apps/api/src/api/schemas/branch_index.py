import uuid
from datetime import datetime
from typing import Literal

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


class BranchMemoryRow(BaseModel):
    """One branch on the Branch Memory screen: what reviews read for it now,
    how it got there, and what keeping it fresh has cost lately."""

    repo_id: uuid.UUID
    repo_full_name: str
    # Disconnected repositories keep their indexes but can't be rebuilt.
    repo_active: bool
    branch_name: str
    is_default_branch: bool
    # ready: the latest build is in use. building: a build is queued or
    # running (an older ready index, if any, is still in use). failed: the
    # latest build failed (likewise). not_indexed: never built.
    state: Literal["ready", "building", "failed", "not_indexed"]
    has_ready_index: bool
    head_sha: str | None
    node_count: int
    edge_count: int
    unresolved_count: int
    # The stored list is capped, so a count at the cap means "at least".
    unresolved_capped: bool
    # Unresolved references as a share of all references found (resolved
    # edges plus unresolved); None without a ready index.
    unresolved_rate: float | None
    build_duration_ms: int | None
    built_at: datetime | None
    last_update: IndexUpdateLogPublic | None
    full_rebuilds_30d: int
    incremental_updates_30d: int
