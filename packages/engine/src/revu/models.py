"""Pydantic contracts produced by the engine.

These are the in-memory shapes an agent/pipeline run produces. They are the
contract between `packages/engine` and its callers (the worker, tests, and
eventually the eval harness if that track is ever added) — persistence into
Postgres (Stage 1's SQLAlchemy models in `apps/api`) is a separate concern.
Changing a field here requires updating every caller; there is no migration
for a Pydantic model.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingCategory(StrEnum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    PERFORMANCE = "performance"
    CONVENTION = "convention"
    TEST_ADEQUACY = "test_adequacy"
    MAINTAINABILITY = "maintainability"


class EvidenceItem(BaseModel):
    """One piece of evidence a finding cites — a location the agent looked at
    and the reason it was relevant. This is what renders as the evidence
    trail in the PR analysis view.
    """

    model_config = ConfigDict(frozen=True)

    file_path: str
    line_start: int
    line_end: int
    reason: str


class Finding(BaseModel):
    """A single review comment, independent of where it came from or whether
    it has been posted anywhere yet.
    """

    file_path: str
    line_start: int
    line_end: int
    category: FindingCategory
    severity: Severity
    message: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    agent_name: str


class ContextItem(BaseModel):
    """One piece of retrieved context assembled into a bundle for an agent,
    with the reason it was retrieved (graph traversal, BM25, embedding
    similarity, ...) so the evidence trail can point back to it.
    """

    file_path: str
    line_start: int
    line_end: int
    content: str
    retrieval_reason: str
    score: float = 0.0


class ContextBundle(BaseModel):
    items: list[ContextItem] = Field(default_factory=list)
    total_tokens: int = 0
    retrieval_strategy: str


class RunResult(BaseModel):
    """What a completed analysis run produced. The worker persists this into
    `analysis_runs` / `findings` / `context_bundles` rows.

    `stopped_reason` is `None` when the agent reached a real verdict. A set
    value (e.g. "max_tool_rounds_reached") means it ran out of investigation
    budget before producing one — `findings` may be incomplete, not a
    confident "nothing wrong here". Stage 7's cross-file agent is the first
    caller that can set this; a caller-facing "re-run to continue" action
    (surfaced through the API/UI) is intentionally not built yet — this
    field exists so that wiring has something concrete to key off of later
    without another contract change.
    """

    findings: list[Finding] = Field(default_factory=list)
    context_bundle: ContextBundle | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    stopped_reason: str | None = None
