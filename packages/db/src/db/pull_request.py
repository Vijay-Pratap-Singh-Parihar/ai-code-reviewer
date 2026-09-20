import enum
import uuid
from datetime import datetime

from revu.models import FindingCategory, Severity
from sqlalchemy import ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db._enum import pg_enum
from db.base import Base
from db.mixins import TZDateTime, UUIDPrimaryKeyMixin


class PullRequestState(enum.StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class AnalysisRunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class DeveloperAction(enum.StrEnum):
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    IGNORED = "ignored"


class PullRequest(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "pull_requests"

    repo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    merge_base_sha: Mapped[str | None] = mapped_column(String(40))
    state: Mapped[PullRequestState] = mapped_column(
        pg_enum(PullRequestState), default=PullRequestState.OPEN, nullable=False
    )
    opened_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
    merged_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    analysis_runs: Mapped[list["AnalysisRun"]] = relationship(
        back_populates="pull_request", cascade="all, delete-orphan"
    )


class AnalysisRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analysis_runs"

    pr_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False
    )
    branch_index_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("branch_index.id", ondelete="SET NULL")
    )
    config_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    status: Mapped[AnalysisRunStatus] = mapped_column(
        pg_enum(AnalysisRunStatus), default=AnalysisRunStatus.QUEUED, nullable=False
    )
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 4), default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    estimated_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_hits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    error: Mapped[str | None] = mapped_column(Text)

    pull_request: Mapped[PullRequest] = relationship(back_populates="analysis_runs")
    findings: Mapped[list["FindingRecord"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    context_bundle: Mapped["ContextBundleRecord | None"] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False
    )


class FindingRecord(UUIDPrimaryKeyMixin, Base):
    """Persisted form of one `revu.models.Finding` produced by an analysis run."""

    __tablename__ = "findings"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    line_start: Mapped[int] = mapped_column(Integer, nullable=False)
    line_end: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[FindingCategory] = mapped_column(pg_enum(FindingCategory), nullable=False)
    severity: Mapped[Severity] = mapped_column(pg_enum(Severity), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    posted_comment_id: Mapped[str | None] = mapped_column(String(64))
    developer_action: Mapped[DeveloperAction | None] = mapped_column(pg_enum(DeveloperAction))
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime)

    run: Mapped[AnalysisRun] = relationship(back_populates="findings")


class ContextBundleRecord(UUIDPrimaryKeyMixin, Base):
    """Persisted form of `revu.models.ContextBundle` for one analysis run."""

    __tablename__ = "context_bundles"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    items_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retrieval_strategy: Mapped[str] = mapped_column(String(100), nullable=False)

    run: Mapped[AnalysisRun] = relationship(back_populates="context_bundle")
