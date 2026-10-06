"""GitHub App integration: linking installations to orgs, keeping the
`repositories` table in sync with what each installation can see, and
turning webhook deliveries into DB updates and queued jobs.

Two trust rules run through this module:

* An installation is linked to an org only after GitHub itself confirms
  that the signed-in user can access it (OAuth `code` -> user token ->
  `GET /user/installations`). The `installation_id` in the setup redirect
  URL is attacker-controllable and is never trusted on its own.
* Webhook payloads are trusted only after `ghapp.verify_signature` passes
  (enforced in the router), and only act on installations already linked.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from arq import ArqRedis
from db.branch_index import BranchIndex, BranchIndexStatus
from db.github import WebhookDelivery
from db.organization import GithubInstallation
from db.pull_request import AnalysisRun, AnalysisRunStatus, PullRequest, PullRequestState
from db.repository import Repository
from ghapp import GitHubClient
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings

logger = logging.getLogger(__name__)

# PR actions that mean "there is (possibly new) code to review".
_REVIEWABLE_ACTIONS = frozenset({"opened", "reopened", "synchronize", "ready_for_review"})


class InstallationNotAccessibleError(Exception):
    """The signed-in GitHub user cannot access the installation they claim."""


class InstallationOwnedByAnotherOrgError(Exception):
    """The installation is already linked to a different revu org."""


# --- linking + repository sync ---------------------------------------------


async def link_installation(
    session: AsyncSession, gh: GitHubClient, *, org_id: uuid.UUID, installation_id: int, code: str
) -> GithubInstallation:
    user_token = await gh.exchange_oauth_code(code)
    if installation_id not in await gh.list_user_installation_ids(user_token):
        raise InstallationNotAccessibleError(installation_id)

    details = await gh.get_installation(installation_id)
    account = details.get("account") or {}

    installation = await session.scalar(
        select(GithubInstallation).where(GithubInstallation.installation_id == installation_id)
    )
    if installation is not None and installation.org_id != org_id:
        raise InstallationOwnedByAnotherOrgError(installation_id)
    if installation is None:
        installation = GithubInstallation(
            org_id=org_id, installation_id=installation_id, installed_at=datetime.now(UTC)
        )
        session.add(installation)
    installation.account_login = str(account.get("login", "unknown"))
    installation.account_type = str(account.get("type", "User"))
    installation.permissions = dict(details.get("permissions") or {})
    await session.flush()

    await resync_installation(session, gh, installation)
    await session.commit()
    await session.refresh(installation)
    return installation


async def resync_installation(
    session: AsyncSession, gh: GitHubClient, installation: GithubInstallation
) -> list[Repository]:
    """Full sync against GitHub: upsert every repo the installation can see
    and deactivate any previously linked repo it no longer can."""
    token = await gh.create_installation_token(installation.installation_id)
    payloads = await gh.list_installation_repositories(token)
    repos = await upsert_repositories(session, installation, payloads)

    seen = {r.id for r in repos}
    stale = await session.scalars(
        select(Repository).where(
            Repository.installation_id == installation.id, Repository.is_active.is_(True)
        )
    )
    for repo in stale:
        if repo.id not in seen:
            repo.is_active = False
    await session.flush()
    return repos


async def upsert_repositories(
    session: AsyncSession, installation: GithubInstallation, payloads: list[dict[str, Any]]
) -> list[Repository]:
    """Match each GitHub repo by its stable numeric ID first, then by name
    (adopting a row the manual diff-paste flow created earlier). A row owned
    by another org is left alone: `full_name` is globally unique, and
    silently moving it would hand that org's history to this one.
    """
    synced: list[Repository] = []
    for payload in payloads:
        github_id = int(payload["id"])
        full_name = str(payload["full_name"])
        repo = await session.scalar(
            select(Repository).where(Repository.github_repo_id == github_id)
        )
        if repo is None:
            repo = await session.scalar(select(Repository).where(Repository.full_name == full_name))
        if repo is not None and repo.org_id != installation.org_id:
            logger.warning("repo %s already belongs to another org; not linking", full_name)
            continue
        if repo is None:
            repo = Repository(org_id=installation.org_id, full_name=full_name)
            session.add(repo)
        repo.github_repo_id = github_id
        repo.full_name = full_name
        repo.installation_id = installation.id
        repo.is_active = True
        if payload.get("default_branch"):
            repo.default_branch = str(payload["default_branch"])
        synced.append(repo)
    await session.flush()
    return synced


# --- webhooks ---------------------------------------------------------------


@dataclass
class WebhookOutcome:
    status: str  # "processed" | "duplicate" | "ignored"
    detail: str = ""
    enqueued: list[str] | None = None


async def record_delivery(
    session: AsyncSession, *, delivery_id: str, event: str, action: str | None
) -> bool:
    """Insert the delivery ID; False means it was already processed. Uses
    ON CONFLICT so two concurrent redeliveries can't both pass the check."""
    result = await session.execute(
        insert(WebhookDelivery)
        .values(id=uuid.uuid4(), delivery_id=delivery_id, event=event, action=action)
        .on_conflict_do_nothing(index_elements=["delivery_id"])
        .returning(WebhookDelivery.id)
    )
    return result.scalar_one_or_none() is not None


async def _installation_by_github_id(
    session: AsyncSession, installation_payload: dict[str, Any] | None
) -> GithubInstallation | None:
    if not installation_payload or "id" not in installation_payload:
        return None
    result: GithubInstallation | None = await session.scalar(
        select(GithubInstallation).where(
            GithubInstallation.installation_id == int(installation_payload["id"])
        )
    )
    return result


async def _linked_repo(
    session: AsyncSession, installation: GithubInstallation, repo_payload: dict[str, Any]
) -> Repository | None:
    repo = await session.scalar(
        select(Repository).where(
            Repository.github_repo_id == int(repo_payload["id"]),
            Repository.installation_id == installation.id,
        )
    )
    if repo is None:
        return None
    # Payloads always carry the current name/default branch; keep ours fresh.
    repo.full_name = str(repo_payload.get("full_name", repo.full_name))
    if repo_payload.get("default_branch"):
        repo.default_branch = str(repo_payload["default_branch"])
    return repo


def _parse_ts(value: object) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


async def upsert_pull_request(
    session: AsyncSession, repo: Repository, pr_payload: dict[str, Any]
) -> PullRequest:
    number = int(pr_payload["number"])
    pr = await session.scalar(
        select(PullRequest).where(PullRequest.repo_id == repo.id, PullRequest.number == number)
    )
    if pr is None:
        pr = PullRequest(repo_id=repo.id, number=number)
        session.add(pr)
    pr.title = str(pr_payload.get("title") or f"PR #{number}")[:500]
    pr.body = pr_payload.get("body") or ""
    pr.author = str((pr_payload.get("user") or {}).get("login", "unknown"))
    pr.base_branch = str(pr_payload["base"]["ref"])
    pr.head_sha = str(pr_payload["head"]["sha"])
    pr.opened_at = _parse_ts(pr_payload.get("created_at")) or datetime.now(UTC)
    pr.merged_at = _parse_ts(pr_payload.get("merged_at"))
    if pr_payload.get("state") == "closed":
        pr.state = PullRequestState.MERGED if pr.merged_at else PullRequestState.CLOSED
    else:
        pr.state = PullRequestState.OPEN
    await session.flush()
    return pr


async def queue_github_review(
    session: AsyncSession,
    redis: ArqRedis,
    *,
    pr: PullRequest,
    agent: str,
    trigger: str,
    branch_index_id: uuid.UUID | None = None,
) -> AnalysisRun:
    run = AnalysisRun(
        pr_id=pr.id,
        branch_index_id=branch_index_id,
        config_snapshot={
            "agent": agent,
            "model": get_settings().revu_model_review,
            "head_sha": pr.head_sha,
            "trigger": trigger,
            "source": "github",
        },
        status=AnalysisRunStatus.QUEUED,
        # Python-side, like BranchIndex.created_at: Postgres now() is
        # transaction-scoped, which would tie runs created in one transaction.
        created_at=datetime.now(UTC),
    )
    session.add(run)
    await session.commit()
    await redis.enqueue_job("review_github_pr", str(run.id))
    return run


async def _already_reviewed(session: AsyncSession, pr: PullRequest) -> bool:
    """A reopen (or a redelivered event) at a head SHA that already has a
    queued/running/succeeded run must not pay for the same review twice."""
    runs = await session.scalars(
        select(AnalysisRun).where(
            AnalysisRun.pr_id == pr.id, AnalysisRun.status != AnalysisRunStatus.FAILED
        )
    )
    return any(r.config_snapshot.get("head_sha") == pr.head_sha for r in runs)


async def _handle_pull_request(
    session: AsyncSession,
    redis: ArqRedis,
    installation: GithubInstallation,
    payload: dict[str, Any],
) -> WebhookOutcome:
    repo = await _linked_repo(session, installation, payload["repository"])
    if repo is None or not repo.is_active:
        return WebhookOutcome("ignored", "repository is not linked")

    pr_payload = payload["pull_request"]
    pr = await upsert_pull_request(session, repo, pr_payload)
    action = payload.get("action")

    if action not in _REVIEWABLE_ACTIONS:
        await session.commit()
        return WebhookOutcome("processed", f"pull request {action}")
    if not repo.auto_review_enabled:
        await session.commit()
        return WebhookOutcome("processed", "auto-review is disabled for this repository")
    if pr_payload.get("draft"):
        await session.commit()
        return WebhookOutcome("processed", "draft pull requests are not auto-reviewed")
    if await _already_reviewed(session, pr):
        await session.commit()
        return WebhookOutcome("processed", "this head commit was already reviewed")

    # Auto-review always uses the cheaper diff-only reviewer; the deeper
    # cross-file review is an explicit, per-PR choice in the UI.
    run = await queue_github_review(session, redis, pr=pr, agent="diff_only", trigger="webhook")
    return WebhookOutcome("processed", "auto-review queued", enqueued=[str(run.id)])


async def queue_github_index(
    session: AsyncSession,
    redis: ArqRedis,
    *,
    repo: Repository,
    branch_name: str,
    target_sha: str | None,
    force_full: bool,
) -> BranchIndex:
    row = BranchIndex(
        repo_id=repo.id,
        branch_name=branch_name,
        head_sha=target_sha,
        status=BranchIndexStatus.PENDING,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    await redis.enqueue_job("sync_and_index_branch", str(row.id), target_sha, force_full)
    return row


async def _handle_push(
    session: AsyncSession,
    redis: ArqRedis,
    installation: GithubInstallation,
    payload: dict[str, Any],
) -> WebhookOutcome:
    repo = await _linked_repo(session, installation, payload["repository"])
    if repo is None or not repo.is_active:
        return WebhookOutcome("ignored", "repository is not linked")
    if payload.get("deleted") or payload.get("ref") != f"refs/heads/{repo.default_branch}":
        await session.commit()
        return WebhookOutcome("ignored", "not a push to the default branch")

    # Only keep branch memory fresh for repos that opted into it by building
    # an index at least once; indexing every connected repo would be waste.
    has_index = await session.scalar(
        select(BranchIndex.id)
        .where(BranchIndex.repo_id == repo.id, BranchIndex.branch_name == repo.default_branch)
        .limit(1)
    )
    if has_index is None:
        await session.commit()
        return WebhookOutcome("processed", "branch is not indexed; nothing to refresh")

    row = await queue_github_index(
        session,
        redis,
        repo=repo,
        branch_name=repo.default_branch,
        target_sha=str(payload["after"]),
        force_full=False,
    )
    return WebhookOutcome("processed", "index refresh queued", enqueued=[str(row.id)])


async def _handle_installation(
    session: AsyncSession, installation: GithubInstallation, action: str | None
) -> WebhookOutcome:
    if action in ("deleted", "suspend"):
        await session.execute(
            update(Repository)
            .where(Repository.installation_id == installation.id)
            .values(is_active=False)
        )
        if action == "deleted":
            await session.delete(installation)
    elif action == "unsuspend":
        await session.execute(
            update(Repository)
            .where(Repository.installation_id == installation.id)
            .values(is_active=True)
        )
    await session.commit()
    return WebhookOutcome("processed", f"installation {action}")


async def _handle_installation_repositories(
    session: AsyncSession, installation: GithubInstallation, payload: dict[str, Any]
) -> WebhookOutcome:
    # The payload omits default_branch; new repos start as "main" and are
    # corrected by the next pull_request/push payload or a manual resync.
    await upsert_repositories(session, installation, payload.get("repositories_added") or [])
    removed_ids = [int(r["id"]) for r in payload.get("repositories_removed") or []]
    if removed_ids:
        await session.execute(
            update(Repository)
            .where(
                Repository.installation_id == installation.id,
                Repository.github_repo_id.in_(removed_ids),
            )
            .values(is_active=False)
        )
    await session.commit()
    return WebhookOutcome("processed", "repositories synced")


async def handle_webhook(
    session: AsyncSession,
    redis: ArqRedis,
    *,
    delivery_id: str,
    event: str,
    payload: dict[str, Any],
) -> WebhookOutcome:
    action = payload.get("action")
    if not await record_delivery(session, delivery_id=delivery_id, event=event, action=action):
        await session.rollback()
        return WebhookOutcome("duplicate", "delivery already processed")

    if event == "ping":
        await session.commit()
        return WebhookOutcome("processed", "pong")

    installation = await _installation_by_github_id(session, payload.get("installation"))
    if installation is None:
        # Not linked to any org yet (e.g. `installation.created` arrives
        # before the user finishes the setup redirect). Record and move on.
        await session.commit()
        return WebhookOutcome("ignored", "installation is not linked to an organization")

    if event == "pull_request":
        return await _handle_pull_request(session, redis, installation, payload)
    if event == "push":
        return await _handle_push(session, redis, installation, payload)
    if event == "installation":
        return await _handle_installation(session, installation, action)
    if event == "installation_repositories":
        return await _handle_installation_repositories(session, installation, payload)

    await session.commit()
    return WebhookOutcome("ignored", f"event '{event}' is not handled")
