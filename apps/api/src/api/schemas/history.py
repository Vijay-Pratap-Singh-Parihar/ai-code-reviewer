import uuid
from datetime import datetime

from db.pull_request import AnalysisRunStatus, PullRequestState
from pydantic import BaseModel, Field


class HistoryRun(BaseModel):
    """One review in the organisation's history, without its findings."""

    id: uuid.UUID
    created_at: datetime
    finished_at: datetime | None
    status: AnalysisRunStatus
    agent: str | None
    model: str | None
    # "github" or "manual"; for GitHub runs, what started it (webhook,
    # manual, retry).
    source: str | None
    trigger: str | None
    repo_id: uuid.UUID
    repo_full_name: str
    pr_number: int
    pr_title: str
    pr_state: PullRequestState
    findings: int
    # Findings per severity ("critical", "high", ...), only those present.
    severities: dict[str, int] = Field(default_factory=dict)
    cost_usd: float
    tokens: int
    github_review_url: str | None
    error: str | None


class HistoryPage(BaseModel):
    runs: list[HistoryRun]
    # Pass as `before` for the next (older) page; None on the last page.
    next_before: datetime | None = None


class AdoptionStats(BaseModel):
    """What happened to reviews and their findings, all time."""

    runs: int
    succeeded: int
    failed: int
    in_progress: int
    pull_requests: int
    merged_pull_requests: int
    posted_to_github: int
    findings: int
    by_severity: dict[str, int]
    # What developers did with findings; "no_feedback" when nothing yet.
    accepted: int
    dismissed: int
    ignored: int
    no_feedback: int
    # accepted / (accepted + dismissed); None before any feedback.
    acceptance_rate: float | None
