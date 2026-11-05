"""History: every review the organisation has run, newest first, with the
filters the History screen offers, and adoption statistics (what became of
reviews and their findings)."""

import uuid
from datetime import datetime
from typing import Any

from db.pull_request import (
    AnalysisRun,
    AnalysisRunStatus,
    DeveloperAction,
    FindingRecord,
    PullRequest,
    PullRequestState,
)
from db.repository import Repository
from revu.models import Severity
from sqlalchemy import Select, distinct, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.history import AdoptionStats, HistoryPage, HistoryRun

__all__ = ["SEVERITY_ORDER", "adoption_stats", "list_history"]

SEVERITY_ORDER = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]


def at_least(severity: Severity) -> list[Severity]:
    return SEVERITY_ORDER[SEVERITY_ORDER.index(severity) :]


def _filtered(
    stmt: Select[Any],
    *,
    org_id: uuid.UUID,
    repo_id: uuid.UUID | None,
    severity: Severity | None = None,
    outcome: AnalysisRunStatus | None = None,
    pr_state: PullRequestState | None = None,
) -> Select[Any]:
    stmt = stmt.where(AnalysisRun.org_id == org_id)
    if repo_id is not None:
        stmt = stmt.where(PullRequest.repo_id == repo_id)
    if outcome is not None:
        stmt = stmt.where(AnalysisRun.status == outcome)
    if pr_state is not None:
        stmt = stmt.where(PullRequest.state == pr_state)
    if severity is not None:
        stmt = stmt.where(
            exists().where(
                FindingRecord.run_id == AnalysisRun.id,
                FindingRecord.severity.in_(at_least(severity)),
            )
        )
    return stmt


async def list_history(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    repo_id: uuid.UUID | None = None,
    severity: Severity | None = None,
    outcome: AnalysisRunStatus | None = None,
    pr_state: PullRequestState | None = None,
    before: datetime | None = None,
    limit: int = 50,
) -> HistoryPage:
    """`severity` keeps runs with at least one finding at that severity or
    above."""
    stmt = _filtered(
        select(AnalysisRun, PullRequest, Repository)
        .join(PullRequest, PullRequest.id == AnalysisRun.pr_id)
        .join(Repository, Repository.id == PullRequest.repo_id),
        org_id=org_id,
        repo_id=repo_id,
        severity=severity,
        outcome=outcome,
        pr_state=pr_state,
    )
    if before is not None:
        stmt = stmt.where(AnalysisRun.created_at < before)
    rows = (
        await session.execute(
            stmt.order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc()).limit(limit + 1)
        )
    ).all()
    page = rows[:limit]

    counts: dict[uuid.UUID, dict[str, int]] = {}
    if page:
        found = await session.execute(
            select(FindingRecord.run_id, FindingRecord.severity, func.count())
            .where(FindingRecord.run_id.in_([run.id for run, _, _ in page]))
            .group_by(FindingRecord.run_id, FindingRecord.severity)
        )
        for run_id, sev, n in found.all():
            counts.setdefault(run_id, {})[str(sev)] = int(n)

    runs = []
    for run, pr, repo in page:
        snapshot = run.config_snapshot or {}
        severities = counts.get(run.id, {})
        runs.append(
            HistoryRun(
                id=run.id,
                created_at=run.created_at,
                finished_at=run.finished_at,
                status=run.status,
                agent=_str(snapshot.get("agent")),
                model=_str(snapshot.get("model")),
                source=_str(snapshot.get("source")),
                trigger=_str(snapshot.get("trigger")),
                repo_id=repo.id,
                repo_full_name=repo.full_name,
                pr_number=pr.number,
                pr_title=pr.title,
                pr_state=pr.state,
                findings=sum(severities.values()),
                severities=severities,
                cost_usd=float(run.cost_usd or 0),
                tokens=(run.tokens_in or 0) + (run.tokens_out or 0),
                github_review_url=run.github_review_url,
                error=run.error,
            )
        )
    next_before = page[-1][0].created_at if len(rows) > limit else None
    return HistoryPage(runs=runs, next_before=next_before)


def _str(value: object) -> str | None:
    return str(value) if value is not None else None


async def adoption_stats(
    session: AsyncSession, org_id: uuid.UUID, *, repo_id: uuid.UUID | None = None
) -> AdoptionStats:
    runs_q = _filtered(
        select(AnalysisRun.status, func.count())
        .join(PullRequest, PullRequest.id == AnalysisRun.pr_id)
        .group_by(AnalysisRun.status),
        org_id=org_id,
        repo_id=repo_id,
    )
    by_status = {status: int(n) for status, n in (await session.execute(runs_q)).all()}

    prs_q = _filtered(
        select(
            func.count(distinct(PullRequest.id)),
            func.count(distinct(PullRequest.id)).filter(
                PullRequest.state == PullRequestState.MERGED
            ),
            func.count(distinct(AnalysisRun.id)).filter(AnalysisRun.posted_at.is_not(None)),
        ).join(PullRequest, PullRequest.id == AnalysisRun.pr_id),
        org_id=org_id,
        repo_id=repo_id,
    )
    prs, merged, posted = (await session.execute(prs_q)).one()

    findings_q = _filtered(
        select(FindingRecord.severity, FindingRecord.developer_action, func.count())
        .join(AnalysisRun, AnalysisRun.id == FindingRecord.run_id)
        .join(PullRequest, PullRequest.id == AnalysisRun.pr_id)
        .group_by(FindingRecord.severity, FindingRecord.developer_action),
        org_id=org_id,
        repo_id=repo_id,
    )
    by_severity: dict[str, int] = {}
    by_action: dict[DeveloperAction | None, int] = {}
    for sev, action, n in (await session.execute(findings_q)).all():
        by_severity[str(sev)] = by_severity.get(str(sev), 0) + int(n)
        by_action[action] = by_action.get(action, 0) + int(n)

    accepted = by_action.get(DeveloperAction.ACCEPTED, 0)
    dismissed = by_action.get(DeveloperAction.DISMISSED, 0)
    judged = accepted + dismissed
    return AdoptionStats(
        runs=sum(by_status.values()),
        succeeded=by_status.get(AnalysisRunStatus.SUCCEEDED, 0),
        failed=by_status.get(AnalysisRunStatus.FAILED, 0),
        in_progress=by_status.get(AnalysisRunStatus.QUEUED, 0)
        + by_status.get(AnalysisRunStatus.RUNNING, 0),
        pull_requests=int(prs),
        merged_pull_requests=int(merged),
        posted_to_github=int(posted),
        findings=sum(by_severity.values()),
        by_severity=by_severity,
        accepted=accepted,
        dismissed=dismissed,
        ignored=by_action.get(DeveloperAction.IGNORED, 0),
        no_feedback=by_action.get(None, 0),
        acceptance_rate=round(accepted / judged, 3) if judged else None,
    )
