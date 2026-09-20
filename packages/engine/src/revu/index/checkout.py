"""Materialise a repository worktree at a given commit SHA.

Uses `git worktree` (via GitPython) rather than `git checkout` so the
indexer never mutates the caller's actual working directory or branch —
important because the API/worker process may be indexing a commit that
isn't (and shouldn't become) the currently checked-out branch.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from git import Repo


class CheckoutError(RuntimeError):
    """Raised when `git worktree add` fails, e.g. an unknown commit SHA."""


def add_worktree(repo_path: Path, commit_sha: str, dest: Path | None = None) -> Path:
    """Create a detached worktree for `commit_sha` under `dest` (a fresh
    temp directory if not given) and return its path.

    The caller is responsible for calling `remove_worktree` afterwards
    (or use `worktree_checkout` below, which does this automatically).
    """
    repo = Repo(repo_path)
    if dest is None:
        dest = Path(tempfile.mkdtemp(prefix="revu-index-")) / "worktree"
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        repo.git.worktree("add", "--detach", str(dest), commit_sha)
    except Exception as exc:  # GitCommandError, or a bad path/sha
        raise CheckoutError(f"failed to create worktree for {commit_sha}: {exc}") from exc

    return dest


def remove_worktree(repo_path: Path, worktree_path: Path) -> None:
    """Remove a worktree created by `add_worktree`, tolerating a worktree
    directory that's already gone (best-effort cleanup)."""
    repo = Repo(repo_path)
    try:
        repo.git.worktree("remove", "--force", str(worktree_path))
    except Exception:
        # The directory may already be gone, or git's worktree metadata may
        # be stale; fall back to a plain filesystem removal + prune so a
        # failed cleanup here never masks the caller's real result.
        shutil.rmtree(worktree_path, ignore_errors=True)
        with contextlib.suppress(Exception):
            repo.git.worktree("prune")


@contextmanager
def worktree_checkout(repo_path: Path, commit_sha: str) -> Iterator[Path]:
    """Context manager: checkout `commit_sha` into a temporary worktree,
    yield its path, and always clean up afterwards.
    """
    worktree_path = add_worktree(repo_path, commit_sha)
    try:
        yield worktree_path
    finally:
        remove_worktree(repo_path, worktree_path)
        shutil.rmtree(worktree_path.parent, ignore_errors=True)
