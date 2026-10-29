"""The harness's own, dataset-independent formats.

Every benchmark (AACR-Bench, Martian, SWR-Bench, our own field data) is
converted into `BenchInstance` records by an adapter, so matching, metrics
and statistics never depend on one dataset's field names.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ContextLevel = Literal["diff", "file", "repo"]
Split = Literal["dev", "test"]


class GroundTruthComment(BaseModel):
    """One human (or expert-verified) review comment: the issue revu should find."""

    path: str
    line_start: int = Field(ge=0)
    line_end: int = Field(ge=0)
    text: str = ""
    category: str | None = None
    severity: str | None = None
    # How much context is needed to spot it (AACR-Bench labels this): only
    # the diff, the rest of the changed file, or other files in the repo.
    context_level: ContextLevel | None = None


class BenchInstance(BaseModel):
    """One pull request to review, with its ground truth."""

    id: str
    repo: str
    language: str = "unknown"
    base_sha: str | None = None
    head_sha: str | None = None
    pr_title: str = ""
    pr_body: str = ""
    diff: str
    ground_truth: list[GroundTruthComment] = Field(default_factory=list)


class PredictedFinding(BaseModel):
    """A finding a system produced for an instance, in a comparable shape."""

    path: str
    line_start: int = Field(ge=0)
    line_end: int = Field(ge=0)
    message: str = ""
    category: str | None = None
    severity: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class RunRecord(BaseModel):
    """One system run on one instance: what it found and what it cost.
    Records are appended to a JSONL file as they finish, so an interrupted
    benchmark resumes instead of paying for finished PRs again."""

    instance_id: str
    config: str
    repeat: int = 0
    findings: list[PredictedFinding] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    tool_rounds: int | None = None
    error: str | None = None
    # Findings before the verifier ran, to measure evidence validity and
    # what the verifier dropped (§10.3).
    raw_findings: list[PredictedFinding] | None = None

    @property
    def ok(self) -> bool:
        return self.error is None
