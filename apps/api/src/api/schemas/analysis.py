import uuid

from db.pull_request import AnalysisRunStatus
from pydantic import BaseModel, Field
from revu.models import FindingCategory, Severity


class AnalysisRequest(BaseModel):
    """Ad-hoc analysis trigger. Until the GitHub App exists (Stage 10), the
    caller supplies the diff directly rather than the server fetching it —
    see IMPLEMENTATION_PLAN.md Stage 3 for why this is an intentional, named
    simplification rather than an oversight.
    """

    repo_full_name: str = Field(min_length=1, max_length=255, examples=["acme/widgets"])
    base_branch: str = Field(default="main", max_length=255)
    head_sha: str = Field(min_length=7, max_length=40)
    pr_number: int = Field(ge=1)
    pr_title: str = Field(min_length=1, max_length=500)
    pr_body: str = ""
    diff: str = Field(min_length=1)


class FindingPublic(BaseModel):
    file_path: str
    line_start: int
    line_end: int
    category: FindingCategory
    severity: Severity
    message: str
    confidence: float
    agent_name: str

    model_config = {"from_attributes": True}


class AnalysisRunPublic(BaseModel):
    id: uuid.UUID
    status: AnalysisRunStatus
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int | None
    error: str | None
    findings: list[FindingPublic] = Field(default_factory=list)
