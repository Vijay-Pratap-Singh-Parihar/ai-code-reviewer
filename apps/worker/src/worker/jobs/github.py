"""ARQ jobs for GitHub-connected repositories.

These jobs fetch what they need from GitHub with the repository's own
installation token: the PR diff over the REST API, and the code itself into
that repository's cache directory (`worker.storage`, `worker.repo_cache`).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from db.branch_index import BranchIndex, BranchIndexStatus, IndexUpdateLog, IndexUpdateMode
from db.pull_request import AnalysisRun, PullRequest
from db.repository import Repository
from db.tenancy import bind_org
from revu.index.checkout import add_worktree, discard_worktree
from sqlalchemy.ext.asyncio import AsyncSession

from worker import repo_cache
from worker.github import installation_token, open_client, remote_url
from worker.jobs.analyze import execute_review, mark_run_failed, mark_run_running
from worker.jobs.index_branch import update_branch_index
from worker.providers import provider_access_from_ctx
from worker.storage import storage_from_ctx

logger = logging.getLogger(__name__)


async def review_github_pr(ctx: dict[str, Any], run_id: str, org_id: str) -> None:
    session_factory = ctx["db_session_factory"]
    storage = storage_from_ctx(ctx)

    async with session_factory() as session:
        await bind_org(session, uuid.UUID(org_id))
        run = await session.get(AnalysisRun, uuid.UUID(run_id))
        if run is None:
            logger.error("review_github_pr: run %s no longer exists", run_id)
            return
        pr = await session.get(PullRequest, run.pr_id)
        repo = await session.get(Repository, pr.repo_id) if pr else None
        if pr is None or repo is None:
            await mark_run_failed(session, run, RuntimeError("pull request or repository is gone"))
            return

        await mark_run_running(session, run)
        agent = str(run.config_snapshot.get("agent", "diff_only"))

        try:
            async with await open_client(ctx, session) as gh:
                token = await installation_token(session, gh, repo)
                pull = await gh.get_pull(token, repo.full_name, pr.number)
                diff = await gh.get_pull_diff(token, repo.full_name, pr.number)
            checkout_from: Path | None = None
            if agent == "cross_file":
                checkout_from = await repo_cache.fetch_refs(
                    dest=storage.repo_cache_dir(repo.org_id, repo.id),
                    remote_url=remote_url(ctx, repo.full_name),
                    token=token,
                    refspecs=[repo_cache.pull_refspec(pr.number)],
                )
                head_sha = await repo_cache.rev_parse(
                    checkout_from, f"refs/remotes/origin/pr/{pr.number}"
                )
            else:
                head_sha = str(pull["head"]["sha"])
        except Exception as exc:
            logger.exception("review_github_pr: could not fetch PR for run %s", run_id)
            await mark_run_failed(session, run, exc)
            return

        # The PR may have moved since the run was queued: review (and record)
        # the head that the fetched diff actually describes.
        pr.head_sha = head_sha
        run.diff_text = diff
        run.config_snapshot = {**run.config_snapshot, "head_sha": head_sha}
        await session.commit()

        if checkout_from is None:
            await execute_review(
                session, run, repo=repo, pr_title=pr.title, pr_body=pr.body or "", diff=diff,
                agent=agent, repo_path=None, storage=storage,
                provider_access=provider_access_from_ctx(ctx),
            )
            return

        try:
            worktree = await asyncio.to_thread(add_worktree, checkout_from, head_sha)
        except Exception as exc:
            logger.exception("review_github_pr: checkout failed for run %s", run_id)
            await mark_run_failed(session, run, exc)
            return
        try:
            await execute_review(
                session, run, repo=repo, pr_title=pr.title, pr_body=pr.body or "", diff=diff,
                agent=agent, repo_path=str(worktree), storage=storage,
                provider_access=provider_access_from_ctx(ctx),
            )
        finally:
            await asyncio.to_thread(discard_worktree, checkout_from, worktree)


async def _fail_index_row(
    session: AsyncSession, row: BranchIndex, *, target_sha: str | None, reason: str
) -> None:
    now = datetime.now(UTC)
    row.status = BranchIndexStatus.FAILED
    row.updated_at = now
    session.add(
        IndexUpdateLog(
            branch_index_id=row.id,
            from_sha=None,
            to_sha=target_sha or "unknown",
            files_changed=0,
            mode=IndexUpdateMode.FULL,
            duration_ms=0,
            reason=reason,
            created_at=now,
        )
    )
    await session.commit()


async def sync_and_index_branch(
    ctx: dict[str, Any],
    branch_index_id: str,
    org_id: str,
    target_sha: str | None,
    force_full: bool,
) -> None:
    """Fetch the branch into the repository's own cache directory, then
    build the index from that checkout (`update_branch_index`)."""
    session_factory = ctx["db_session_factory"]
    storage = storage_from_ctx(ctx)

    async with session_factory() as session:
        await bind_org(session, uuid.UUID(org_id))
        row = await session.get(BranchIndex, uuid.UUID(branch_index_id))
        if row is None:
            logger.error("sync_and_index_branch: row %s no longer exists", branch_index_id)
            return
        repo = await session.get(Repository, row.repo_id)
        if repo is None:
            await _fail_index_row(
                session, row, target_sha=target_sha, reason="repository no longer exists"
            )
            return

        try:
            async with await open_client(ctx, session) as gh:
                token = await installation_token(session, gh, repo)
            path = await repo_cache.fetch_refs(
                dest=storage.repo_cache_dir(repo.org_id, repo.id),
                remote_url=remote_url(ctx, repo.full_name),
                token=token,
                refspecs=[repo_cache.branch_refspec(row.branch_name)],
            )
            sha = target_sha or await repo_cache.rev_parse(
                path, f"refs/remotes/origin/{row.branch_name}"
            )
        except Exception as exc:
            logger.exception("sync_and_index_branch: fetch failed for %s", branch_index_id)
            await _fail_index_row(
                session, row, target_sha=target_sha, reason=f"could not fetch branch: {exc}"
            )
            return

    await update_branch_index(ctx, branch_index_id, org_id, str(path), sha, force_full)
