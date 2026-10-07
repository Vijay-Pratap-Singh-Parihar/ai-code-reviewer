"""GitHub-path worker jobs, end to end against a real database and real git.

GitHub itself is replaced by two fakes: a local git repository standing in
for `github.com/<owner>/<repo>.git` (including a `refs/pull/<n>/head` ref,
which is how GitHub exposes PR heads), and an `httpx.MockTransport` standing
in for the REST API. The reviewer is monkeypatched, so no LLM is called.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from db.branch_index import BranchIndex, BranchIndexStatus, IndexUpdateLog
from db.organization import GithubInstallation, Organization
from db.pull_request import AnalysisRun, AnalysisRunStatus, PullRequest, PullRequestState
from db.repository import Repository
from ghapp import GitHubAppConfig, GitHubClient
from git import Repo
from revu.models import RunResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from worker import repo_cache
from worker.jobs import analyze
from worker.jobs.github import review_github_pr, sync_and_index_branch

FULL_NAME = "acme/widgets"
TOKEN = "ghs_test_installation_token"
FAKE_DIFF = (
    "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
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


def _commit(repo: Repo, filename: str, content: str, message: str) -> str:
    (Path(repo.working_dir) / filename).write_text(content)
    repo.index.add([filename])
    return repo.index.commit(message).hexsha


def _fake_remote(root: Path) -> tuple[str, str]:
    """A git repo at <root>/acme/widgets.git with `main` and PR #1's head."""
    repo = Repo.init(root / "acme" / "widgets.git", initial_branch="main")
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test User")
        cfg.set_value("user", "email", "test@example.com")
    base_sha = _commit(repo, "app.py", "def handler():\n    return 1\n", "base")
    repo.git.checkout("-b", "feature")
    head_sha = _commit(repo, "app.py", "def handler():\n    return 2\n", "change")
    repo.git.update_ref("refs/pull/1/head", head_sha)
    repo.git.checkout("main")
    return base_sha, head_sha


def _github_api(head_sha: str, calls: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": TOKEN})
        if request.url.path == f"/repos/{FULL_NAME}/pulls/1":
            assert request.headers["Authorization"] == f"token {TOKEN}"
            if request.headers["Accept"] == "application/vnd.github.diff":
                return httpx.Response(200, text=FAKE_DIFF)
            return httpx.Response(200, json={"number": 1, "head": {"sha": head_sha}})
        return httpx.Response(404, json={"message": "Not Found"})

    return httpx.MockTransport(handler)


@pytest.fixture
def github_ctx(
    worker_ctx: dict[str, Any], app_config: GitHubAppConfig, tmp_path: Path
) -> dict[str, Any]:
    base_sha, head_sha = _fake_remote(tmp_path / "remote")
    calls: list[str] = []
    transport = _github_api(head_sha, calls)
    worker_ctx.update(
        github_client_factory=lambda: GitHubClient(app_config, transport=transport),
        git_base_url=str(tmp_path / "remote"),
        repo_cache_dir=tmp_path / "cache",
        test_base_sha=base_sha,
        test_head_sha=head_sha,
        test_calls=calls,
    )
    return worker_ctx


async def _make_connected_repo(session: AsyncSession) -> Repository:
    org = Organization(name="Acme Inc")
    session.add(org)
    await session.flush()
    installation = GithubInstallation(
        org_id=org.id,
        installation_id=987654321012,  # > 2^31: exercises the BigInteger column
        account_login="acme",
        installed_at=datetime.now(UTC),
    )
    session.add(installation)
    await session.flush()
    repo = Repository(
        org_id=org.id,
        installation_id=installation.id,
        github_repo_id=555,
        full_name=FULL_NAME,
        default_branch="main",
    )
    session.add(repo)
    await session.flush()
    return repo


async def _make_github_run(
    session: AsyncSession, repo: Repository, *, agent: str = "diff_only"
) -> AnalysisRun:
    pr = PullRequest(
        repo_id=repo.id,
        number=1,
        title="Return two",
        body="",
        author="octocat",
        base_branch="main",
        head_sha="0" * 40,  # stale on purpose: the job must record the real head
        state=PullRequestState.OPEN,
        opened_at=datetime.now(UTC),
    )
    session.add(pr)
    await session.flush()
    run = AnalysisRun(
        pr_id=pr.id,
        config_snapshot={"agent": agent, "model": "fake-model", "head_sha": pr.head_sha},
        status=AnalysisRunStatus.QUEUED,
    )
    session.add(run)
    await session.commit()
    return run


async def test_review_github_pr_diff_only_fetches_diff_and_records_head(
    worker_db_session: AsyncSession,
    github_ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = await _make_connected_repo(worker_db_session)
    run = await _make_github_run(worker_db_session, repo)
    seen: dict[str, object] = {}

    async def fake_review_diff(**kwargs: object) -> RunResult:
        seen.update(kwargs)
        return RunResult(findings=[], tokens_in=10, tokens_out=5, cost_usd=0.0, latency_ms=1)

    monkeypatch.setattr(analyze, "review_diff", fake_review_diff)

    await review_github_pr(github_ctx, str(run.id))

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.SUCCEEDED, run.error
    assert run.diff_text == FAKE_DIFF
    assert seen["diff"] == FAKE_DIFF
    assert seen["pr_title"] == "Return two"
    assert run.config_snapshot["head_sha"] == github_ctx["test_head_sha"]
    pr = await worker_db_session.get(PullRequest, run.pr_id)
    assert pr is not None and pr.head_sha == github_ctx["test_head_sha"]
    # diff_only never needs the code itself, so nothing was cloned.
    assert not (Path(github_ctx["repo_cache_dir"]) / "acme").exists()


async def test_review_github_pr_cross_file_reviews_a_checkout_of_the_pr_head(
    worker_db_session: AsyncSession,
    github_ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = await _make_connected_repo(worker_db_session)
    run = await _make_github_run(worker_db_session, repo, agent="cross_file")
    seen: dict[str, object] = {}

    async def fake_run_cross_file(**kwargs: Any) -> list[object]:
        path = Path(kwargs["repo_path"])
        seen["repo_path"] = path
        seen["content"] = (path / "app.py").read_text()
        return []

    monkeypatch.setattr(analyze, "_run_cross_file", fake_run_cross_file)

    await review_github_pr(github_ctx, str(run.id))

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.SUCCEEDED, run.error
    assert seen["content"] == "def handler():\n    return 2\n"  # the PR head, not main
    assert not Path(str(seen["repo_path"])).exists()  # worktree cleaned up
    cached = repo_cache.cache_path(Path(github_ctx["repo_cache_dir"]), FULL_NAME)
    # The token never lands in the cached repo's config.
    assert TOKEN not in (cached / ".git" / "config").read_text()


async def test_review_github_pr_marks_failed_when_github_errors(
    worker_db_session: AsyncSession,
    github_ctx: dict[str, Any],
    app_config: GitHubAppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = await _make_connected_repo(worker_db_session)
    run = await _make_github_run(worker_db_session, repo)
    broken = httpx.MockTransport(lambda r: httpx.Response(401, json={"message": "Bad creds"}))
    github_ctx["github_client_factory"] = lambda: GitHubClient(app_config, transport=broken)

    async def should_not_run(**kwargs: object) -> RunResult:
        raise AssertionError("reviewer must not run without a diff")

    monkeypatch.setattr(analyze, "review_diff", should_not_run)

    await review_github_pr(github_ctx, str(run.id))

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.FAILED
    assert "Bad creds" in (run.error or "")


async def test_review_github_pr_fails_fast_when_app_not_configured(
    worker_db_session: AsyncSession, github_ctx: dict[str, Any]
) -> None:
    repo = await _make_connected_repo(worker_db_session)
    run = await _make_github_run(worker_db_session, repo)
    github_ctx["github_client_factory"] = None

    await review_github_pr(github_ctx, str(run.id))

    await worker_db_session.refresh(run)
    assert run.status == AnalysisRunStatus.FAILED
    assert "not configured" in (run.error or "")


async def test_sync_and_index_branch_builds_a_ready_index_from_the_fetched_branch(
    worker_db_session: AsyncSession,
    github_ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("REVU_INDEX_STORAGE_DIR", str(tmp_path / "blobs"))
    repo = await _make_connected_repo(worker_db_session)
    row = BranchIndex(
        repo_id=repo.id,
        branch_name="main",
        status=BranchIndexStatus.PENDING,
        created_at=datetime.now(UTC),
    )
    worker_db_session.add(row)
    await worker_db_session.commit()

    await sync_and_index_branch(github_ctx, str(row.id), None, False)

    await worker_db_session.refresh(row)
    assert row.status == BranchIndexStatus.READY
    assert row.head_sha == github_ctx["test_base_sha"]  # main's head, resolved after fetch
    assert row.node_count > 0


async def test_sync_and_index_branch_fails_row_for_unconnected_repo(
    worker_db_session: AsyncSession, github_ctx: dict[str, Any]
) -> None:
    org = Organization(name="Manual Org")
    worker_db_session.add(org)
    await worker_db_session.flush()
    repo = Repository(org_id=org.id, full_name="manual/only")
    worker_db_session.add(repo)
    await worker_db_session.flush()
    row = BranchIndex(
        repo_id=repo.id,
        branch_name="main",
        status=BranchIndexStatus.PENDING,
        created_at=datetime.now(UTC),
    )
    worker_db_session.add(row)
    await worker_db_session.commit()

    await sync_and_index_branch(github_ctx, str(row.id), None, False)

    await worker_db_session.refresh(row)
    assert row.status == BranchIndexStatus.FAILED
    logs = (
        await worker_db_session.scalars(
            select(IndexUpdateLog).where(IndexUpdateLog.branch_index_id == row.id)
        )
    ).all()
    assert logs and "not connected" in (logs[0].reason or "")


async def test_jobs_return_quietly_for_missing_rows(github_ctx: dict[str, Any]) -> None:
    await review_github_pr(github_ctx, str(uuid.uuid4()))
    await sync_and_index_branch(github_ctx, str(uuid.uuid4()), None, False)


def test_cache_path_rejects_unsafe_names(tmp_path: Path) -> None:
    for name in ("../etc", "a/../../b", "a/b/c", "noslash", "a/b;rm"):
        with pytest.raises(repo_cache.RepoCacheError):
            repo_cache.cache_path(tmp_path, name)


def test_git_errors_never_echo_the_token(tmp_path: Path) -> None:
    dest = tmp_path / "x"
    with pytest.raises(repo_cache.RepoCacheError) as excinfo:
        repo_cache._fetch_sync(
            dest, str(tmp_path / "does-not-exist.git"), "ghs_secret", ["+refs/heads/main:refs/x"]
        )
    assert "ghs_secret" not in str(excinfo.value)
    assert "ghs_secret" not in (dest / ".git" / "config").read_text()


async def test_open_client_uses_credentials_stored_by_the_manifest_flow(
    worker_db_session: AsyncSession, worker_ctx: dict[str, Any], app_config: GitHubAppConfig
) -> None:
    from db.github import GitHubAppCredentials
    from ghapp import SecretBox
    from worker.github import open_client

    box = SecretBox("worker-test-key")
    worker_db_session.add(
        GitHubAppCredentials(
            id=1,
            app_id=777,
            slug="revu-test",
            name="revu-test",
            owner_login="vijay",
            html_url="https://github.com/apps/revu-test",
            client_id="Iv23li",
            client_secret_enc=box.encrypt("cs"),
            webhook_secret_enc=box.encrypt("ws"),
            private_key_enc=box.encrypt(app_config.private_key_pem),
        )
    )
    await worker_db_session.flush()
    worker_ctx.update(github_client_factory=None, credential_encryption_key="worker-test-key")

    async with await open_client(worker_ctx, worker_db_session) as gh:
        assert gh._config.app_id == "777"
        assert gh._config.private_key_pem == app_config.private_key_pem
