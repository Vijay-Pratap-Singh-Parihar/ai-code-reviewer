"""Posting a finished run to its pull request as one GitHub review.

Used two ways: right after an automatic (webhook-triggered) review, and as
the `post_github_review` job behind the "Post review to GitHub" button.

- **Idempotent.** A run remembers the review it was posted as
  (`github_review_id`); asking again does nothing.
- **Never loses findings.** Findings GitHub won't take inline go into the
  review body (`revu.github_review`). If GitHub still rejects the inline
  comments (422, e.g. the PR moved on since the review), it is posted again
  with every finding in the body.
- **Explains failures.** A missing `pull_requests: write` permission (403)
  or anything else GitHub refuses is stored on the run (`post_error`) and in
  the audit log, so the UI can say what to fix.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from db.audit import AuditAction, record_audit
from db.organization import User
from db.pull_request import AnalysisRun, AnalysisRunStatus, FindingRecord, PullRequest
from db.repository import Repository
from db.tenancy import bind_org
from ghapp import GitHubError
from revu.github_review import ReviewEvidence, ReviewFinding, build_review, summary_only
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from worker.github import installation_token, open_client

logger = logging.getLogger(__name__)

MISSING_PERMISSION = (
    "the GitHub App can't write pull request reviews: give it the "
    "'Pull requests: Read and write' permission in its GitHub settings, then accept the "
    "new permission on the installation"
)


def _review_finding(row: FindingRecord) -> ReviewFinding:
    return ReviewFinding(
        path=row.file_path,
        line_start=row.line_start,
        line_end=row.line_end,
        severity=str(row.severity),
        category=str(row.category),
        message=row.message,
        confidence=float(row.confidence) if row.confidence is not None else None,
        evidence=tuple(
            ReviewEvidence(
                path=str(e.get("file_path", "")),
                line_start=int(str(e.get("line_start", 0))),
                line_end=int(str(e.get("line_end", 0))),
                reason=str(e.get("reason", "")),
            )
            for e in (row.evidence_json or [])
        ),
    )


def _model_label(run: AnalysisRun) -> str | None:
    snapshot = run.config_snapshot or {}
    model = snapshot.get("model")
    provider = snapshot.get("provider_name")
    if model and provider:
        return f"{model} ({provider})"
    return str(model) if model else None


async def post_run_to_github(
    ctx: dict[str, Any], session: AsyncSession, run: AnalysisRun, *, actor_id: uuid.UUID | None
) -> bool:
    """Post `run` as a review on its pull request. True when it is posted
    (now or before); False with `run.post_error` set otherwise."""
    if run.github_review_id is not None:
        return True
    if run.status != AnalysisRunStatus.SUCCEEDED:
        run.post_error = "only a finished review can be posted"
        await session.commit()
        return False
    pr = await session.get(PullRequest, run.pr_id)
    repo = await session.get(Repository, pr.repo_id) if pr else None
    if pr is None or repo is None or repo.github_repo_id is None:
        run.post_error = "this review isn't for a GitHub pull request"
        await session.commit()
        return False

    rows = list(await session.scalars(select(FindingRecord).where(FindingRecord.run_id == run.id)))
    findings = [_review_finding(r) for r in rows]
    label = _model_label(run)
    payload = build_review(findings, run.diff_text or "", model_label=label)
    commit_id = str((run.config_snapshot or {}).get("head_sha") or pr.head_sha)

    try:
        async with await open_client(ctx, session) as gh:
            token = await installation_token(session, gh, repo)
            try:
                review = await gh.create_review(
                    token, repo.full_name, pr.number, payload.to_request(commit_id)
                )
            except GitHubError as exc:
                if exc.status_code != 422 or not payload.comments:
                    raise
                logger.warning(
                    "run %s: inline comments rejected (%s); posting summary only", run.id, exc
                )
                payload = summary_only(findings, model_label=label)
                review = await gh.create_review(
                    token, repo.full_name, pr.number, payload.to_request(commit_id)
                )
    except Exception as exc:
        reason = (
            MISSING_PERMISSION
            if isinstance(exc, GitHubError) and exc.status_code == 403
            else (f"GitHub refused the review: {exc}" if isinstance(exc, GitHubError) else str(exc))
        )
        run.post_error = reason[:1000]
        record_audit(
            session,
            org_id=run.org_id,
            actor_id=actor_id,
            action=AuditAction.REVIEW_POST_FAILED,
            target=f"pull_request:{repo.full_name}#{pr.number}",
            metadata={"run_id": str(run.id), "reason": reason[:300]},
        )
        await session.commit()
        logger.warning("run %s: posting to GitHub failed: %s", run.id, reason)
        return False

    run.github_review_id = int(review["id"])
    run.github_review_url = str(review.get("html_url") or "") or None
    run.posted_at = datetime.now(UTC)
    run.post_error = None
    record_audit(
        session,
        org_id=run.org_id,
        actor_id=actor_id,
        action=AuditAction.REVIEW_POSTED,
        target=f"pull_request:{repo.full_name}#{pr.number}",
        metadata={
            "run_id": str(run.id),
            "inline": payload.inline,
            "in_summary": payload.in_summary,
            "review_url": run.github_review_url,
        },
    )
    await session.commit()
    return True


async def post_github_review(
    ctx: dict[str, Any], run_id: str, org_id: str, actor_id: str | None = None
) -> None:
    """The job behind "Post review to GitHub"."""
    async with ctx["db_session_factory"]() as session:
        await bind_org(session, uuid.UUID(org_id))
        run = await session.get(AnalysisRun, uuid.UUID(run_id))
        if run is None:
            return
        # The requester may have been removed since clicking; then the post
        # is recorded as a system action rather than failing.
        actor = await session.get(User, uuid.UUID(actor_id)) if actor_id else None
        await post_run_to_github(ctx, session, run, actor_id=actor.id if actor else None)


async def auto_post(ctx: dict[str, Any], session: AsyncSession, run: AnalysisRun) -> None:
    """After a webhook-triggered review: post it without anyone clicking.
    Manual reviews wait for the button, so people can look first."""
    if (run.config_snapshot or {}).get("trigger") != "webhook":
        return
    await session.refresh(run)
    if run.status == AnalysisRunStatus.SUCCEEDED:
        await post_run_to_github(ctx, session, run, actor_id=None)
