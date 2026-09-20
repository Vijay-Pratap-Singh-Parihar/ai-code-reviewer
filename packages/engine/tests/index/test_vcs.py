"""Real-git-repo tests for `revu.index.vcs`: fast-forward detection,
merge-base resolution, and branch-head lookup. Every test constructs an
actual git repository under `tmp_path` (via GitPython) rather than mocking
git — force-push/rebase detection is exactly the kind of logic that's easy
to get subtly wrong against a fake, per the task's testing bar.
"""

from pathlib import Path

import pytest
from git import Repo
from revu.index.vcs import (
    GitRefError,
    NoCommonAncestorError,
    compute_merge_base,
    is_ancestor,
    resolve_branch_head,
    resolve_pr_merge_base,
)


def _init_repo(tmp_path: Path) -> Repo:
    repo = Repo.init(tmp_path)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test User")
        cfg.set_value("user", "email", "test@example.com")
    return repo


def _commit(repo: Repo, filename: str, content: str, message: str) -> str:
    path = Path(repo.working_dir) / filename
    path.write_text(content)
    repo.index.add([filename])
    return repo.index.commit(message).hexsha


def test_resolve_branch_head_returns_the_tip_commit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, "a.py", "x = 1\n", "first")
    assert resolve_branch_head(tmp_path, repo.active_branch.name) == sha


def test_resolve_branch_head_falls_back_to_origin_remote_ref(tmp_path: Path) -> None:
    upstream_dir = tmp_path / "upstream"
    upstream_dir.mkdir()
    upstream = _init_repo(upstream_dir)
    sha = _commit(upstream, "a.py", "x = 1\n", "first")

    clone_dir = tmp_path / "clone"
    clone = Repo.clone_from(upstream_dir, clone_dir)
    branch_name = clone.active_branch.name
    # A bare fetch gives us `origin/<branch>` without a local branch of the
    # same name checked out — the exact shape this app-first track's
    # "caller supplies repo_path directly" design can produce. Detach HEAD
    # first since git refuses to delete the currently checked-out branch.
    clone.git.checkout("--detach")
    clone.git.branch("-D", branch_name)

    assert resolve_branch_head(clone_dir, upstream.active_branch.name) == sha


def test_resolve_branch_head_raises_for_unknown_branch(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    with pytest.raises(GitRefError):
        resolve_branch_head(tmp_path, "does-not-exist")


def test_is_ancestor_true_for_a_normal_fast_forward(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    old_sha = _commit(repo, "a.py", "x = 1\n", "first")
    new_sha = _commit(repo, "a.py", "x = 2\n", "second")

    assert is_ancestor(tmp_path, old_sha, new_sha) is True


def test_is_ancestor_false_for_a_rewritten_history(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    old_sha = _commit(repo, "a.py", "x = 1\n", "first")
    forward_sha = _commit(repo, "a.py", "x = 2\n", "second")

    # Rewrite history: reset back to the first commit and make a *different*
    # second commit — `forward_sha` is now an orphaned, rewritten-away tip.
    repo.git.reset("--hard", old_sha)
    rewritten_sha = _commit(repo, "a.py", "x = 999\n", "rewritten second")

    assert rewritten_sha != forward_sha
    # The old tip is no longer an ancestor of the rewritten tip's sibling
    # history — going from forward_sha to rewritten_sha is not a fast-forward.
    assert is_ancestor(tmp_path, forward_sha, rewritten_sha) is False
    # And the reverse holds for the common base.
    assert is_ancestor(tmp_path, old_sha, rewritten_sha) is True


def test_is_ancestor_raises_for_an_unknown_sha(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, "a.py", "x = 1\n", "first")
    with pytest.raises(GitRefError):
        is_ancestor(tmp_path, "0" * 40, sha)


def test_compute_merge_base_matches_a_real_diverging_history(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, "a.py", "x = 1\n", "base")
    main_branch = repo.active_branch.name

    repo.git.checkout("-b", "feature")
    feature_sha = _commit(repo, "feature.py", "y = 1\n", "feature work")

    repo.git.checkout(main_branch)
    main_sha = _commit(repo, "main.py", "z = 1\n", "main moved on")

    computed = compute_merge_base(tmp_path, main_sha, feature_sha)
    expected = repo.git.merge_base(main_sha, feature_sha).strip()

    assert computed == expected == base_sha


def test_compute_merge_base_raises_for_unrelated_histories(tmp_path: Path) -> None:
    repo_a_dir = tmp_path / "a"
    repo_a_dir.mkdir()
    repo_a = _init_repo(repo_a_dir)
    sha_a = _commit(repo_a, "a.py", "x = 1\n", "a")

    repo_b_dir = tmp_path / "b"
    repo_b_dir.mkdir()
    repo_b = _init_repo(repo_b_dir)
    sha_b = _commit(repo_b, "b.py", "y = 1\n", "b")

    # Graft repo_b's commit into repo_a's object store without any shared
    # history (both are unborn/root commits).
    repo_a.git.fetch(str(repo_b_dir), sha_b)

    with pytest.raises(NoCommonAncestorError):
        compute_merge_base(repo_a_dir, sha_a, "FETCH_HEAD")


def test_resolve_pr_merge_base_reports_full_coverage_when_index_is_current(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_sha = _commit(repo, "a.py", "x = 1\n", "base")
    repo.git.checkout("-b", "feature")
    pr_head_sha = _commit(repo, "feature.py", "y = 1\n", "pr work")

    info = resolve_pr_merge_base(
        tmp_path,
        base_branch=base_sha,  # a plain commit-ish works as well as a branch name
        pr_head_sha=pr_head_sha,
        indexed_head_sha=base_sha,
    )

    assert info.merge_base_sha == base_sha
    assert info.index_covers_merge_base is True
    assert info.branch_advanced_since_merge_base is False


def test_resolve_pr_merge_base_flags_drift_when_index_predates_the_merge_base(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    very_old_sha = _commit(repo, "old.py", "a = 1\n", "very old")
    merge_base_sha = _commit(repo, "a.py", "x = 1\n", "base")
    repo.git.checkout("-b", "feature")
    pr_head_sha = _commit(repo, "feature.py", "y = 1\n", "pr work")

    info = resolve_pr_merge_base(
        tmp_path,
        base_branch=merge_base_sha,
        pr_head_sha=pr_head_sha,
        indexed_head_sha=very_old_sha,
    )

    assert info.merge_base_sha == merge_base_sha
    assert info.index_covers_merge_base is False


def test_resolve_pr_merge_base_reports_branch_advanced_since_merge_base(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    merge_base_sha = _commit(repo, "a.py", "x = 1\n", "base")
    repo.git.checkout("-b", "feature")
    pr_head_sha = _commit(repo, "feature.py", "y = 1\n", "pr work")

    repo.git.checkout(next(h.name for h in repo.heads if h.name != "feature"))
    current_main_sha = _commit(repo, "main.py", "z = 1\n", "main moved on since the PR forked")

    info = resolve_pr_merge_base(
        tmp_path,
        base_branch=merge_base_sha,
        pr_head_sha=pr_head_sha,
        indexed_head_sha=current_main_sha,
    )

    assert info.merge_base_sha == merge_base_sha
    assert info.index_covers_merge_base is True
    assert info.branch_advanced_since_merge_base is True
