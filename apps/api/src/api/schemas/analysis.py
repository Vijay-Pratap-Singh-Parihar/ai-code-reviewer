import uuid
from typing import Literal

from db.pull_request import AnalysisRunStatus
from pydantic import BaseModel, Field
from revu.models import EvidenceItem, FindingCategory, Severity


class AnalysisRequest(BaseModel):
    """Ad-hoc analysis of a pasted diff. The caller supplies the diff, so
    this path never touches repository files and is always `diff_only`.

    Cross-file review reads the repository itself, so it is only offered for
    GitHub-connected repos (`POST /repos/{repo_id}/pulls/{number}/analysis`),
    where the worker owns the checkout. This endpoint used to accept a
    `repo_path` on the worker's filesystem for that; it was removed because
    it let any user point the worker at any directory it could read,
    including another tenant's clone.
    """

    model_config = {"extra": "forbid"}

    repo_full_name: str = Field(min_length=1, max_length=255, examples=["acme/widgets"])
    base_branch: str = Field(default="main", max_length=255)
    head_sha: str = Field(min_length=7, max_length=40)
    pr_number: int = Field(ge=1)
    pr_title: str = Field(min_length=1, max_length=500)
    pr_body: str = ""
    diff: str = Field(min_length=1)
    agent: Literal["diff_only"] = "diff_only"


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
