"""Posting runs to GitHub as pull request reviews, against a real database
and a fake GitHub REST API (`httpx.MockTransport`). No LLM is called."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from db.ledger import AuditLog
from db.organization import GithubInstallation, Organization
from db.pull_request import (
    AnalysisRun,
    AnalysisRunStatus,
    FindingRecord,
    PullRequest,
    PullRequestState,
)
from db.repository import Repository
from db.tenancy import bind_org
from ghapp import GitHubAppConfig, GitHubClient
from revu.models import FindingCategory, Severity
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from worker.jobs.post_review import MISSING_PERMISSION, auto_post, post_github_review

FULL_NAME = "acme/widgets"
HEAD = "c" * 40
DIFF = (
    "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
    "@@ -1,3 +1,3 @@\n a = 1\n-b = 2\n+b = 3\n c = 4\n"
)


@pytest.fixture(scope="module")
def app_config() -> GitHubAppConfig:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return GitHubAppConfig(app_id="1", private_key_pem=pem)


class FakeGitHub:
    """Answers the token and review endpoints; `responses` lists the status
    codes successive review POSTs get."""

    def __init__(self, responses: list[int]) -> None:
        self.responses = responses
        self.reviews: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "ghs_test"})
        if request.url.path == f"/repos/{FULL_NAME}/pulls/7/reviews":
            self.reviews.append(json.loads(request.content))
            status = self.responses.pop(0)
            if status == 200:
                return httpx.Response(
                    200, json={"id": 4242, "html_url": "https://github.com/acme/widgets/pull/7#r1"}
                )
            return httpx.Response(status, json={"message": "nope"})
        return httpx.Response(404, json={"message": "Not Found"})


def _ctx(
    worker_ctx: dict[str, Any], app_config: GitHubAppConfig, fake: FakeGitHub
) -> dict[str, Any]:
    transport = httpx.MockTransport(fake.handler)
    worker_ctx["github_client_factory"] = lambda: GitHubClient(app_config, transport=transport)
    return worker_ctx


async def _finished_run(session: AsyncSession, *, trigger: str = "manual") -> AnalysisRun:
    org = Organization(name="Acme")
    session.add(org)
    await session.flush()
    await bind_org(session, org.id)
    installation = GithubInstallation(
        org_id=org.id,
        installation_id=uuid.uuid4().int % 10**12,
        account_login="acme",
        installed_at=datetime.now(UTC),
    )
    session.add(installation)
    await session.flush()
    repo = Repository(
        org_id=org.id, installation_id=installation.id, github_repo_id=1, full_name=FULL_NAME
    )
    session.add(repo)
    await session.flush()
    pr = PullRequest(
        repo_id=repo.id,
        number=7,
        title="t",
        author="a",
        base_branch="main",
        head_sha=HEAD,
        state=PullRequestState.OPEN,
        opened_at=datetime.now(UTC),
    )
    session.add(pr)
    await session.flush()
    run = AnalysisRun(
        pr_id=pr.id,
        status=AnalysisRunStatus.SUCCEEDED,
        diff_text=DIFF,
        config_snapshot={
            "source": "github",
            "trigger": trigger,
            "head_sha": HEAD,
            "model": "qwen3.5:9b",
            "provider_name": "Local Ollama",
        },
    )
    session.add(run)
    await session.flush()
    for line, message in ((2, "b changed"), (30, "unrelated")):
        session.add(
            FindingRecord(
                run_id=run.id,
                file_path="app.py",
                line_start=line,
                line_end=line,
                category=FindingCategory.CORRECTNESS,
                severity=Severity.HIGH,
                message=message,
                evidence_json=[
                    {"file_path": "app.py", "line_start": line, "line_end": line, "reason": "r"}
                ],
                confidence=0.9,
                agent_name="diff_only",
            )
        )
    await session.commit()
    return run


async def _audit(session: AsyncSession) -> list[str]:
    return [a.action for a in await session.scalars(select(AuditLog).order_by(AuditLog.at))]


async def test_a_run_is_posted_once_with_inline_and_summary_findings(
    worker_db_session: AsyncSession, worker_ctx: dict[str, Any], app_config: GitHubAppConfig
) -> None:
    run = await _finished_run(worker_db_session)
    fake = FakeGitHub([200])
    ctx = _ctx(worker_ctx, app_config, fake)

    await post_github_review(ctx, str(run.id), str(run.org_id))
    await post_github_review(ctx, str(run.id), str(run.org_id))  # asked again: no-op

    await worker_db_session.refresh(run)
    assert len(fake.reviews) == 1
    [review] = fake.reviews
    assert review["event"] == "COMMENT" and review["commit_id"] == HEAD
    assert [c["line"] for c in review["comments"]] == [2]
    assert "`app.py:30`" in review["body"] and "qwen3.5:9b (Local Ollama)" in review["body"]
    assert run.github_review_id == 4242
    assert run.github_review_url.endswith("#r1") and run.posted_at is not None
    assert await _audit(worker_db_session) == ["review.posted"]


async def test_rejected_inline_comments_fall_back_to_a_summary_only_review(
    worker_db_session: AsyncSession, worker_ctx: dict[str, Any], app_config: GitHubAppConfig
) -> None:
    run = await _finished_run(worker_db_session)
    fake = FakeGitHub([422, 200])

    await post_github_review(_ctx(worker_ctx, app_config, fake), str(run.id), str(run.org_id))

    await worker_db_session.refresh(run)
    assert [len(r["comments"]) for r in fake.reviews] == [1, 0]
    assert "`app.py:2`" in fake.reviews[1]["body"]
    assert run.github_review_id == 4242


async def test_a_missing_write_permission_is_explained_not_raised(
    worker_db_session: AsyncSession, worker_ctx: dict[str, Any], app_config: GitHubAppConfig
) -> None:
    run = await _finished_run(worker_db_session)

    await post_github_review(
        _ctx(worker_ctx, app_config, FakeGitHub([403])),
        str(run.id),
        str(run.org_id),
        str(uuid.uuid4()),
    )

    await worker_db_session.refresh(run)
    assert run.github_review_id is None
    assert run.post_error == MISSING_PERMISSION
    assert await _audit(worker_db_session) == ["review.post_failed"]


async def test_only_webhook_reviews_are_posted_automatically(
    worker_db_session: AsyncSession, worker_ctx: dict[str, Any], app_config: GitHubAppConfig
) -> None:
    manual = await _finished_run(worker_db_session, trigger="manual")
    fake = FakeGitHub([200])
    ctx = _ctx(worker_ctx, app_config, fake)

    await auto_post(ctx, worker_db_session, manual)
    assert fake.reviews == []

    automatic = await _finished_run(worker_db_session, trigger="webhook")
    await auto_post(ctx, worker_db_session, automatic)
    await worker_db_session.refresh(automatic)
    assert len(fake.reviews) == 1 and automatic.github_review_id == 4242


async def test_unfinished_runs_are_not_posted(
    worker_db_session: AsyncSession, worker_ctx: dict[str, Any], app_config: GitHubAppConfig
) -> None:
    run = await _finished_run(worker_db_session)
    run.status = AnalysisRunStatus.FAILED
    await worker_db_session.commit()
    fake = FakeGitHub([200])

    await post_github_review(_ctx(worker_ctx, app_config, fake), str(run.id), str(run.org_id))

    await worker_db_session.refresh(run)
    assert fake.reviews == [] and run.post_error == "only a finished review can be posted"
