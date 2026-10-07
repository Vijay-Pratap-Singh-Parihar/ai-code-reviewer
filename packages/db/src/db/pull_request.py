import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from revu.models import FindingCategory, Severity
from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db._enum import pg_enum
from db.base import Base
from db.mixins import CreatedAtMixin, TZDateTime, UUIDPrimaryKeyMixin, inherited_org_id

if TYPE_CHECKING:
    from db.repository import Repository


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
    # Webhook deliveries for the same PR can race; the constraint makes the
    # upsert in api.services.github safe rather than relying on a pre-check.
    __table_args__ = (
        UniqueConstraint("repo_id", "number", name="uq_pull_requests_repo_number"),
        UniqueConstraint("id", "org_id", name="uq_pull_requests_id_org"),
        ForeignKeyConstraint(
            ["repo_id", "org_id"],
            ["repositories.id", "repositories.org_id"],
            ondelete="CASCADE",
            name="fk_pull_requests_repo_org",
        ),
    )

    org_id: Mapped[uuid.UUID] = inherited_org_id()
    repo_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
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

    repository: Mapped["Repository"] = relationship()
    analysis_runs: Mapped[list["AnalysisRun"]] = relationship(
        back_populates="pull_request", cascade="all, delete-orphan"
    )


class AnalysisRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        UniqueConstraint("id", "org_id", name="uq_analysis_runs_id_org"),
        ForeignKeyConstraint(
            ["pr_id", "org_id"],
            ["pull_requests.id", "pull_requests.org_id"],
            ondelete="CASCADE",
            name="fk_analysis_runs_pr_org",
        ),
    )

    org_id: Mapped[uuid.UUID] = inherited_org_id()
    pr_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
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
    # The exact diff this run reviewed, so the PR view can render it from the
    # server instead of relying on the browser that triggered the run.
    diff_text: Mapped[str | None] = mapped_column(Text)

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
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "org_id"],
            ["analysis_runs.id", "analysis_runs.org_id"],
            ondelete="CASCADE",
            name="fk_findings_run_org",
        ),
    )

    org_id: Mapped[uuid.UUID] = inherited_org_id()
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
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
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "org_id"],
            ["analysis_runs.id", "analysis_runs.org_id"],
            ondelete="CASCADE",
            name="fk_context_bundles_run_org",
        ),
    )

    org_id: Mapped[uuid.UUID] = inherited_org_id()
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    items_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retrieval_strategy: Mapped[str] = mapped_column(String(100), nullable=False)

    run: Mapped[AnalysisRun] = relationship(back_populates="context_bundle")
