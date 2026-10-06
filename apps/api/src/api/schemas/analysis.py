import uuid
from typing import Literal

from db.pull_request import AnalysisRunStatus
from pydantic import BaseModel, Field, model_validator
from revu.models import EvidenceItem, FindingCategory, Severity


class AnalysisRequest(BaseModel):
    """Ad-hoc analysis trigger. Until the GitHub App exists (Stage 10), the
    caller supplies the diff directly rather than the server fetching it —
    see IMPLEMENTATION_PLAN.md Stage 3 for why this is an intentional, named
    simplification rather than an oversight.

    `agent` is the caller's own cost/depth choice, not something the server
    escalates automatically — `cross_file` runs roughly 4x more expensive
    per Stage 7's live comparison, and the architecture doc's tiered-routing
    philosophy (§3) treats that as a decision each caller should make
    explicitly, not one the server should make on their behalf. `diff_only`
    needs no repository access at all (Stage 3's design); `cross_file` needs
    both a `repo_path` (a checkout the *worker* can read — see
    `IndexTriggerRequest`'s docstring for the same convention) and a `ready`
    branch index already built for `(repo_full_name, base_branch)` via
    `POST /repos/index` — there is no on-demand indexing fallback here.
    """

    repo_full_name: str = Field(min_length=1, max_length=255, examples=["acme/widgets"])
    base_branch: str = Field(default="main", max_length=255)
    head_sha: str = Field(min_length=7, max_length=40)
    pr_number: int = Field(ge=1)
    pr_title: str = Field(min_length=1, max_length=500)
    pr_body: str = ""
    diff: str = Field(min_length=1)
    agent: Literal["diff_only", "cross_file"] = "diff_only"
    repo_path: str | None = Field(
        default=None,
        min_length=1,
        examples=["/tmp/acme-widgets"],
        description="Required when agent='cross_file'; a path on the worker's filesystem.",
    )

    @model_validator(mode="after")
    def _cross_file_requires_repo_path(self) -> "AnalysisRequest":
        if self.agent == "cross_file" and not self.repo_path:
            raise ValueError("repo_path is required when agent='cross_file'")
        return self


class FindingPublic(BaseModel):
    """`evidence` is the PR analysis view's evidence trail — which files the
    agent looked at and why it cited them. It mirrors `FindingRecord.evidence_json`
    (JSONB) under its real DB column name via `validation_alias`, rather than
    renaming the column to match the API, since nothing else reads it.
    """

    file_path: str
    line_start: int
    line_end: int
    category: FindingCategory
    severity: Severity
    message: str
    confidence: float
    agent_name: str
    evidence: list[EvidenceItem] = Field(default_factory=list, validation_alias="evidence_json")

    model_config = {"from_attributes": True, "populate_by_name": True}


class AnalysisRunPublic(BaseModel):
    id: uuid.UUID
    status: AnalysisRunStatus
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int | None
    error: str | None
    findings: list[FindingPublic] = Field(default_factory=list)
    # Context for the PR analysis view (Stage 10): with these the page no
    # longer depends on metadata the triggering browser kept in sessionStorage.
    agent: str | None = None
    repo_full_name: str | None = None
    pr_number: int | None = None
    pr_title: str | None = None
    base_branch: str | None = None
    head_sha: str | None = None
    diff: str | None = None
