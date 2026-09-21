"""Tests for `worker.jobs.index_branch`.

Two layers, matching the task's testing bar:
- `_decide_and_build` (pure, DB-free) is tested directly against real git
  repos built with GitPython, covering the full/incremental decision matrix
  (first build, force-push, manual force_full, missing blob) without needing
  Postgres at all.
- `update_branch_index` (the actual ARQ job) is tested end to end against a
  real Postgres session (the `worker_db_session`/`worker_ctx` fixtures, same
  pattern as `test_analyze.py`), proving the full row lifecycle: status
  transitions, `IndexUpdateLog` rows, and that a "get current index" read
  never has to reason about a half-written row because none is ever created.
"""

from pathlib import Path

import pytest
from db.branch_index import BranchIndex, BranchIndexStatus, IndexUpdateLog, IndexUpdateMode
from db.organization import Organization
from db.repository import Repository
from git import Repo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from worker.jobs import index_branch


def _init_repo(path: Path) -> Repo:
    path.mkdir(parents=True, exist_ok=True)
    repo = Repo.init(path)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test User")
        cfg.set_value("user", "email", "test@example.com")
    return repo


def _commit(repo: Repo, filename: str, content: str, message: str) -> str:
    path = Path(repo.working_dir) / filename
    path.write_text(content)
    repo.index.add([filename])
    return repo.index.commit(message).hexsha


# --------------------------------------------------------------------------
# `_decide_and_build` — pure decision logic, no database.
# --------------------------------------------------------------------------


def test_decide_and_build_first_build_is_full(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, "a.py", "def f():\n    return 1\n", "first")

    mode, reason, result, files_changed = index_branch._decide_and_build(
        tmp_path,
        repo_identifier="acme/widgets",
        branch_name="main",
        target_sha=sha,
        previous=None,
        force_full=False,
    )

    assert mode == IndexUpdateMode.FULL
    assert reason is not None and "first successful" in reason
    assert result.commit_sha == sha
    assert files_changed == result.files_indexed


def test_decide_and_build_fast_forward_is_incremental(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `_decide_and_build` looks up the previous result blob via
    # `index_result_path`'s env-based default storage dir (it has no
    # storage_dir parameter of its own — see its docstring), so the saved
    # blob must live where that default lookup will find it.
    monkeypatch.setenv("REVU_INDEX_STORAGE_DIR", str(tmp_path / ".blobs"))

    repo = _init_repo(tmp_path)
    sha_1 = _commit(repo, "a.py", "def f():\n    return 1\n", "first")
    sha_2 = _commit(repo, "a.py", "def f():\n    return 2\n", "second")

    from revu.index.graph import build_index
    from revu.index.store import save_index_result

    old_result = build_index(tmp_path, sha_1)
    save_index_result(
        old_result, repo_identifier="acme/widgets", branch_name="main", head_sha=sha_1
    )
    previous = BranchIndex(
        repo_id=None, branch_name="main", head_sha=sha_1, status=BranchIndexStatus.READY
    )

    mode, reason, result, files_changed = index_branch._decide_and_build(
        tmp_path,
        repo_identifier="acme/widgets",
        branch_name="main",
        target_sha=sha_2,
        previous=previous,
        force_full=False,
    )

    assert mode == IndexUpdateMode.INCREMENTAL
    assert reason is None
    assert result.commit_sha == sha_2
    assert files_changed == 1


def test_decide_and_build_force_full_overrides_fast_forward(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha_1 = _commit(repo, "a.py", "def f():\n    return 1\n", "first")
    sha_2 = _commit(repo, "a.py", "def f():\n    return 2\n", "second")

    previous = BranchIndex(
        repo_id=None, branch_name="main", head_sha=sha_1, status=BranchIndexStatus.READY
    )

    mode, reason, _result, _files_changed = index_branch._decide_and_build(
        tmp_path,
        repo_identifier="acme/widgets",
        branch_name="main",
        target_sha=sha_2,
        previous=previous,
        force_full=True,
    )

    assert mode == IndexUpdateMode.FULL
    assert reason == "manual full rebuild requested"


def test_decide_and_build_non_fast_forward_forces_full_rebuild(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha_1 = _commit(repo, "a.py", "def f():\n    return 1\n", "first")
    forward_sha = _commit(repo, "a.py", "def f():\n    return 2\n", "second")

    repo.git.reset("--hard", sha_1)
    rewritten_sha = _commit(repo, "a.py", "def f():\n    return 999\n", "rewritten second")
    assert rewritten_sha != forward_sha

    previous = BranchIndex(
        repo_id=None, branch_name="main", head_sha=forward_sha, status=BranchIndexStatus.READY
    )

    mode, reason, result, _files_changed = index_branch._decide_and_build(
        tmp_path,
        repo_identifier="acme/widgets",
        branch_name="main",
        target_sha=rewritten_sha,
        previous=previous,
        force_full=False,
    )

    assert mode == IndexUpdateMode.FULL
    assert reason is not None and "non-fast-forward" in reason
    assert result.commit_sha == rewritten_sha


def test_decide_and_build_falls_back_to_full_when_previous_blob_is_missing(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    sha_1 = _commit(repo, "a.py", "def f():\n    return 1\n", "first")
    sha_2 = _commit(repo, "a.py", "def f():\n    return 2\n", "second")

    # No `save_index_result` call for sha_1 — the sidecar blob was never
    # written (or has since been cleaned up), so the incremental prerequisite
    # is missing even though history is a clean fast-forward.
    previous = BranchIndex(
        repo_id=None, branch_name="main", head_sha=sha_1, status=BranchIndexStatus.READY
    )

    mode, reason, result, _files_changed = index_branch._decide_and_build(
        tmp_path,
        repo_identifier="acme/widgets",
        branch_name="main",
        target_sha=sha_2,
        previous=previous,
        force_full=False,
    )

    assert mode == IndexUpdateMode.FULL
    assert reason is not None and "previous index snapshot unavailable" in reason
    assert result.commit_sha == sha_2


# --------------------------------------------------------------------------
# `update_branch_index` — the real ARQ job, end to end against Postgres.
# --------------------------------------------------------------------------


async def _make_repo(session: AsyncSession, full_name: str = "acme/widgets") -> Repository:
    org = Organization(name="Acme Inc")
    session.add(org)
    await session.flush()
    repo = Repository(org_id=org.id, full_name=full_name)
    session.add(repo)
    await session.flush()
    return repo


async def _make_pending_row(
    session: AsyncSession, repo: Repository, *, branch_name: str = "main"
) -> BranchIndex:
    row = BranchIndex(
        repo_id=repo.id, branch_name=branch_name, head_sha=None, status=BranchIndexStatus.PENDING
    )
    session.add(row)
    await session.flush()
    return row


async def _logs_for(session: AsyncSession, branch_index_id: object) -> list[IndexUpdateLog]:
    return list(
        (
            await session.scalars(
                select(IndexUpdateLog)
                .where(IndexUpdateLog.branch_index_id == branch_index_id)
                .order_by(IndexUpdateLog.created_at)
            )
        ).all()
    )


async def test_update_branch_index_first_build_succeeds_and_is_full(
    worker_db_session: AsyncSession,
    worker_ctx: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REVU_INDEX_STORAGE_DIR", str(tmp_path / "blobs"))
    repo_dir = tmp_path / "repo"
    git_repo = _init_repo(repo_dir)
    sha = _commit(git_repo, "a.py", "def f():\n    return 1\n", "first")

    repo_row = await _make_repo(worker_db_session)
    row = await _make_pending_row(worker_db_session, repo_row)
    await worker_db_session.commit()

    await index_branch.update_branch_index(worker_ctx, str(row.id), str(repo_dir), sha, False)

    await worker_db_session.refresh(row)
    assert row.status == BranchIndexStatus.READY
    assert row.head_sha == sha
    assert row.node_count >= 1
    assert row.graph_ref is not None
    assert row.built_at is not None

    logs = await _logs_for(worker_db_session, row.id)
    assert len(logs) == 1
    assert logs[0].mode == IndexUpdateMode.FULL
    assert logs[0].from_sha is None
    assert logs[0].to_sha == sha


async def test_update_branch_index_second_build_is_incremental(
    worker_db_session: AsyncSession,
    worker_ctx: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REVU_INDEX_STORAGE_DIR", str(tmp_path / "blobs"))
    repo_dir = tmp_path / "repo"
    git_repo = _init_repo(repo_dir)
    sha_1 = _commit(git_repo, "a.py", "def f():\n    return 1\n", "first")

    repo_row = await _make_repo(worker_db_session)
    row_1 = await _make_pending_row(worker_db_session, repo_row)
    await worker_db_session.commit()
    await index_branch.update_branch_index(worker_ctx, str(row_1.id), str(repo_dir), sha_1, False)
    await worker_db_session.refresh(row_1)
    assert row_1.status == BranchIndexStatus.READY

    sha_2 = _commit(
        git_repo, "a.py", "def f():\n    return 2\n\ndef g():\n    return f()\n", "second"
    )
    row_2 = await _make_pending_row(worker_db_session, repo_row)
    await worker_db_session.commit()
    await index_branch.update_branch_index(worker_ctx, str(row_2.id), str(repo_dir), sha_2, False)

    await worker_db_session.refresh(row_2)
    assert row_2.status == BranchIndexStatus.READY
    assert row_2.head_sha == sha_2

    logs = await _logs_for(worker_db_session, row_2.id)
    assert len(logs) == 1
    assert logs[0].mode == IndexUpdateMode.INCREMENTAL
    assert logs[0].from_sha == sha_1
    assert logs[0].to_sha == sha_2

    # The older `ready` row must be left alone, not deleted or downgraded —
    # both rows coexist, and "current" is whichever is most recently ready.
    await worker_db_session.refresh(row_1)
    assert row_1.status == BranchIndexStatus.READY


async def test_update_branch_index_detects_force_push_and_does_a_full_rebuild(
    worker_db_session: AsyncSession,
    worker_ctx: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REVU_INDEX_STORAGE_DIR", str(tmp_path / "blobs"))
    repo_dir = tmp_path / "repo"
    git_repo = _init_repo(repo_dir)
    sha_1 = _commit(git_repo, "a.py", "def f():\n    return 1\n", "first")

    repo_row = await _make_repo(worker_db_session)
    row_1 = await _make_pending_row(worker_db_session, repo_row)
    await worker_db_session.commit()
    await index_branch.update_branch_index(worker_ctx, str(row_1.id), str(repo_dir), sha_1, False)

    forward_sha = _commit(git_repo, "a.py", "def f():\n    return 2\n", "second")
    row_2 = await _make_pending_row(worker_db_session, repo_row)
    await worker_db_session.commit()
    await index_branch.update_branch_index(
        worker_ctx, str(row_2.id), str(repo_dir), forward_sha, False
    )
    await worker_db_session.refresh(row_2)
    assert row_2.status == BranchIndexStatus.READY

    git_repo.git.reset("--hard", sha_1)
    rewritten_sha = _commit(git_repo, "a.py", "def f():\n    return 999\n", "rewritten")
    row_3 = await _make_pending_row(worker_db_session, repo_row)
    await worker_db_session.commit()
    await index_branch.update_branch_index(
        worker_ctx, str(row_3.id), str(repo_dir), rewritten_sha, False
    )

    await worker_db_session.refresh(row_3)
    assert row_3.status == BranchIndexStatus.READY
    logs = await _logs_for(worker_db_session, row_3.id)
    assert len(logs) == 1
    assert logs[0].mode == IndexUpdateMode.FULL
    assert logs[0].reason is not None and "non-fast-forward" in logs[0].reason
    assert logs[0].from_sha is None  # a full rebuild never claims an incremental base


async def test_update_branch_index_resolves_branch_head_when_no_sha_given(
    worker_db_session: AsyncSession,
    worker_ctx: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REVU_INDEX_STORAGE_DIR", str(tmp_path / "blobs"))
    repo_dir = tmp_path / "repo"
    git_repo = _init_repo(repo_dir)
    sha = _commit(git_repo, "a.py", "def f():\n    return 1\n", "first")
    branch_name = git_repo.active_branch.name

    repo_row = await _make_repo(worker_db_session)
    row = await _make_pending_row(worker_db_session, repo_row, branch_name=branch_name)
    await worker_db_session.commit()

    await index_branch.update_branch_index(worker_ctx, str(row.id), str(repo_dir), None, False)

    await worker_db_session.refresh(row)
    assert row.status == BranchIndexStatus.READY
    assert row.head_sha == sha


async def test_update_branch_index_marks_failed_on_build_error(
    worker_db_session: AsyncSession,
    worker_ctx: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REVU_INDEX_STORAGE_DIR", str(tmp_path / "blobs"))
    not_a_repo = tmp_path / "not-a-git-repo"
    not_a_repo.mkdir()

    repo_row = await _make_repo(worker_db_session)
    row = await _make_pending_row(worker_db_session, repo_row)
    await worker_db_session.commit()

    await index_branch.update_branch_index(
        worker_ctx, str(row.id), str(not_a_repo), "a" * 40, False
    )

    await worker_db_session.refresh(row)
    assert row.status == BranchIndexStatus.FAILED
    logs = await _logs_for(worker_db_session, row.id)
    assert len(logs) == 1
    assert logs[0].reason is not None and "build failed" in logs[0].reason


async def test_update_branch_index_persists_unresolved_symbols(
    worker_db_session: AsyncSession,
    worker_ctx: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REVU_INDEX_STORAGE_DIR", str(tmp_path / "blobs"))
    repo_dir = tmp_path / "repo"
    git_repo = _init_repo(repo_dir)
    # `undefined_thing()` has no definition anywhere this indexer can see —
    # a genuinely unresolved call, per calls.py's documented behaviour.
    sha = _commit(
        git_repo,
        "a.py",
        "def f():\n    return undefined_thing()\n",
        "first",
    )

    repo_row = await _make_repo(worker_db_session)
    row = await _make_pending_row(worker_db_session, repo_row)
    await worker_db_session.commit()

    await index_branch.update_branch_index(worker_ctx, str(row.id), str(repo_dir), sha, False)

    await worker_db_session.refresh(row)
    assert row.status == BranchIndexStatus.READY
    assert len(row.unresolved_symbols) >= 1


async def test_update_branch_index_returns_early_when_row_missing(
    worker_ctx: dict[str, object],
) -> None:
    # Must not raise even though the row doesn't exist.
    await index_branch.update_branch_index(
        worker_ctx, "00000000-0000-0000-0000-000000000000", "/nonexistent", "a" * 40, False
    )
