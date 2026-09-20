from pathlib import Path

import pytest
from git import Repo
from revu.index.checkout import CheckoutError, add_worktree, remove_worktree, worktree_checkout


@pytest.fixture
def small_repo(tmp_path: Path) -> Path:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test User")
        cfg.set_value("user", "email", "test@example.com")

    (repo_dir / "a.py").write_text("VALUE = 1\n")
    repo.index.add(["a.py"])
    repo.index.commit("first commit")

    (repo_dir / "a.py").write_text("VALUE = 2\n")
    repo.index.add(["a.py"])
    repo.index.commit("second commit")

    return repo_dir


def test_worktree_checkout_materialises_file_content_at_given_sha(small_repo: Path) -> None:
    repo = Repo(small_repo)
    first_sha = repo.git.rev_list("--max-parents=0", "HEAD").strip()

    with worktree_checkout(small_repo, first_sha) as worktree_path:
        assert (worktree_path / "a.py").read_text() == "VALUE = 1\n"

    # Cleaned up afterwards.
    assert not worktree_path.exists()


def test_worktree_checkout_reflects_head_by_default(small_repo: Path) -> None:
    with worktree_checkout(small_repo, "HEAD") as worktree_path:
        assert (worktree_path / "a.py").read_text() == "VALUE = 2\n"


def test_add_worktree_raises_checkout_error_for_unknown_sha(small_repo: Path) -> None:
    with pytest.raises(CheckoutError):
        add_worktree(small_repo, "0" * 40)


def test_remove_worktree_is_idempotent_on_missing_directory(small_repo: Path) -> None:
    worktree_path = add_worktree(small_repo, "HEAD")
    remove_worktree(small_repo, worktree_path)
    # Calling it again on an already-removed worktree must not raise.
    remove_worktree(small_repo, worktree_path)
