import re
import uuid
from datetime import UTC, datetime
from typing import Any

from db.organization import User
from db.provider import ModelTier
from db.pull_request import AnalysisRun, AnalysisRunStatus, PullRequest, PullRequestState
from db.repository import Repository
from db.usage import ensure_within_budget
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.schemas.analysis import AnalysisRequest
from api.services.providers import route_snapshot
from api.services.repositories import get_or_create_repository

__all__ = [
    "create_analysis_run",
    "get_run_for_org",
    "run_notices",
]

_SHOWN_SKIPPED = 3

_STOPPED = {
    "output_token_limit": "The model's answer hit its length limit; some findings may be missing.",
    "final_answer_malformed_after_retry": (
        "The model's answer couldn't be read; some findings may be missing."
    ),
    "max_tool_rounds_reached": (
        "The cross-file agent ran out of investigation steps before giving its verdict."
    ),
}


def run_notices(snapshot: dict[str, Any]) -> list[str]:
    """What the run page should tell its reader about how the review went,
    from the notes the worker left in the run's config snapshot."""
    notices: list[str] = []
    if snapshot.get("agent_fallback"):
        notices.append("Reviewed from the diff only: the configured model can't call tools.")
    skipped = [str(p) for p in snapshot.get("skipped_files") or []]
    if skipped:
        names = ", ".join(skipped[:_SHOWN_SKIPPED])
        more = f" and {len(skipped) - _SHOWN_SKIPPED} more" if len(skipped) > _SHOWN_SKIPPED else ""
        noun = "file" if len(skipped) == 1 else "files"
        notices.append(f"Left out {len(skipped)} lockfile or generated {noun}: {names}{more}.")
    retries = snapshot.get("retries")
    if isinstance(retries, int) and retries > 0:
        times = "once" if retries == 1 else f"{retries} times"
        notices.append(f"The AI provider was briefly unavailable; the review was retried {times}.")
    parts = snapshot.get("parts")
    if isinstance(parts, int) and parts > 1:
        notices.append(f"Large diff: reviewed in {parts} parts.")
    reason = str(snapshot.get("stopped_reason") or "")
    failed = re.match(r"parts_failed \((\d+) of (\d+)\)", reason)
    if failed:
        notices.append(
            f"{failed.group(1)} of {failed.group(2)} parts got no answer from the model; "
            "files in those parts have no findings."
        )
    elif reason:
        key = reason.split(" ", 1)[0]
        notices.append(_STOPPED.get(key, f"The review stopped early ({reason})."))
    return notices


async def _get_or_create_pull_request(
    session: AsyncSession, *, repo: Repository, user: User, body: AnalysisRequest
) -> PullRequest:
    pr = await session.scalar(
        select(PullRequest).where(
            PullRequest.repo_id == repo.id, PullRequest.number == body.pr_number
        )
    )
    if pr is not None:
        pr.title = body.pr_title
        pr.body = body.pr_body
        pr.head_sha = body.head_sha
        pr.base_branch = body.base_branch
        return pr

    pr = PullRequest(
        repo_id=repo.id,
        org_id=repo.org_id,
        number=body.pr_number,
        title=body.pr_title,
        body=body.pr_body,
        author=user.email,
        base_branch=body.base_branch,
        head_sha=body.head_sha,
        state=PullRequestState.OPEN,
        opened_at=datetime.now(UTC),
    )
    session.add(pr)
    await session.flush()
    return pr


async def create_analysis_run(
    session: AsyncSession, *, user: User, body: AnalysisRequest
) -> AnalysisRun:
    # Fails before anything is written when the organisation hasn't chosen a
    # review model yet (NoModelRouteError) or has reached its monthly budget
    # (BudgetExceededError).
    await ensure_within_budget(session, user.org_id)
    model = await route_snapshot(session, user.org_id, ModelTier.REVIEW)
    repo = await get_or_create_repository(
        session, org_id=user.org_id, full_name=body.repo_full_name
    )
    pr = await _get_or_create_pull_request(session, repo=repo, user=user, body=body)

    run = AnalysisRun(
        pr_id=pr.id,
        org_id=pr.org_id,
        config_snapshot={
            "agent": body.agent,
            **model,
            "head_sha": body.head_sha,
            "source": "manual",
        },
        status=AnalysisRunStatus.QUEUED,
        diff_text=body.diff,
        created_at=datetime.now(UTC),
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def get_run_for_org(
    session: AsyncSession, *, run_id: uuid.UUID, org_id: uuid.UUID
) -> AnalysisRun | None:
    """Loads a run's findings only if it belongs to the caller's org — the
    multi-tenancy enforcement point the architecture doc calls for at the
    query layer, done here via a join rather than trusting a bare `run_id`.
    """
    stmt = (
        select(AnalysisRun)
        .join(PullRequest, AnalysisRun.pr_id == PullRequest.id)
        .join(Repository, PullRequest.repo_id == Repository.id)
        .options(
            selectinload(AnalysisRun.findings),
            selectinload(AnalysisRun.pull_request).selectinload(PullRequest.repository),
        )
        .where(AnalysisRun.id == run_id, Repository.org_id == org_id)
    )
    result: AnalysisRun | None = await session.scalar(stmt)
    return result
