import uuid
from datetime import datetime

from db.branch_index import BranchIndexStatus, IndexUpdateMode
from pydantic import BaseModel, Field


class IndexTriggerRequest(BaseModel):
    """Trigger a branch-index build. Like `AnalysisRequest` (Stage 3), the
    caller supplies what the server would otherwise have to fetch from
    GitHub itself — here, a filesystem path to a git repository the worker
    process can read (there is no real webhook/App integration until Stage
    10; see IMPLEMENTATION_PLAN.md's stage map). `repo_path` therefore needs
    to be a path inside the *worker's* filesystem, not necessarily the
    caller's — for the Docker-composed deployment that means a path inside
    the worker container, not the host.
    """

    repo_full_name: str = Field(min_length=1, max_length=255, examples=["acme/widgets"])
    branch_name: str = Field(default="main", min_length=1, max_length=255)
    repo_path: str = Field(min_length=1, examples=["/tmp/acme-widgets"])
    target_sha: str | None = Field(default=None, min_length=7, max_length=40)
    force_full: bool = Field(
        default=False,
        description=(
            "Force a full rebuild even if the update would otherwise be a safe "
            "fast-forward incremental update. This is also the manual-rebuild "
            "trigger the future Branch Memory screen calls."
        ),
    )


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
