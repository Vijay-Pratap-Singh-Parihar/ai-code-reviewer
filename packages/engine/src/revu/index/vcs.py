"""Git plumbing helpers for branch memory: fast-forward detection, merge-base
resolution, and branch-head lookup.

These are deliberately separated from `checkout.py`/`incremental.py` because
they don't touch a worktree at all — they answer questions about a repo's
*history* (ref graph), not its file contents, which is exactly what Stage 5's
row lifecycle (force-push detection) and Stage 6's context retrieval
(merge-base resolution) each need as a plain, DB-independent function.

See `Product_Architecture_FullStack.md` §2 for the two subtleties this module
exists to handle: "Merge-base, not head" and "Force-push and rebase
invalidate the delta."
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from git import GitCommandError, Repo


class GitRefError(RuntimeError):
    """A ref/SHA could not be resolved in the given repository (unknown
    branch, unknown commit, or a shallow clone that doesn't have the commit
    in question). Distinct from a bare `GitCommandError` so callers can
    catch exactly this "the input was bad" case and decide how to recover
    (e.g. fall back to a full rebuild) rather than swallowing every possible
    git failure.
    """


class NoCommonAncestorError(GitRefError):
    """Raised by `compute_merge_base` when the two commits share no history
    at all (unrelated histories) — a real, if rare, possibility once force-
    pushes are in play.
    """


def resolve_branch_head(repo_path: Path, branch_name: str) -> str:
    """Resolve `branch_name` to its current commit SHA in `repo_path`.

    Tries the plain ref first (a local branch, or any ref git can resolve
    directly), then `origin/<branch_name>` — since this app-first track has
    no GitHub App integration yet, `repo_path` may be a plain clone where the
    tracked branch only exists as a remote-tracking ref, not a local branch.
    """
    repo = Repo(repo_path)
    for candidate in (branch_name, f"origin/{branch_name}"):
        try:
            sha: str = repo.git.rev_parse(candidate).strip()
            return sha
        except GitCommandError:
            continue
    raise GitRefError(f"could not resolve branch {branch_name!r} in {repo_path}")


def is_ancestor(repo_path: Path, ancestor_sha: str, descendant_sha: str) -> bool:
    """True if `ancestor_sha` is an ancestor of (or equal to) `descendant_sha`
    — i.e. advancing from `ancestor_sha` to `descendant_sha` is a fast-forward.

    False (not raised) for a plain "no, it isn't" answer (`git merge-base
    --is-ancestor` exits 1). A `GitRefError` is raised only when one of the
    SHAs doesn't resolve at all in this repository, which is a different,
    worse problem than "history was rewritten" and callers should handle
    separately (see `worker.jobs.index_branch._decide_and_build`).
    """
    repo = Repo(repo_path)
    try:
        repo.git.merge_base("--is-ancestor", ancestor_sha, descendant_sha)
        return True
    except GitCommandError as exc:
        if exc.status == 1:
            return False
        raise GitRefError(
            f"could not compare {ancestor_sha!r} and {descendant_sha!r} in {repo_path}: {exc}"
        ) from exc


def compute_merge_base(repo_path: Path, sha_a: str, sha_b: str) -> str:
    """The real `git merge-base` between two commit-ish refs."""
    repo = Repo(repo_path)
    try:
        output: str = repo.git.merge_base(sha_a, sha_b)
    except GitCommandError as exc:
        if exc.status == 1:
            raise NoCommonAncestorError(
                f"{sha_a!r} and {sha_b!r} share no common history in {repo_path}"
            ) from exc
        raise GitRefError(
            f"merge-base failed for {sha_a!r}/{sha_b!r} in {repo_path}: {exc}"
        ) from exc
    return output.strip()


@dataclass(frozen=True)
class MergeBaseInfo:
    """What Stage 6's context retrieval needs to decide whether the branch
    index it has on hand actually covers a PR's merge base, per the
    architecture doc: "Store the merge-base SHA on the analysis run and,
    where the snapshot has drifted, either walk the index backwards or note
    the drift in the run record."
    """

    merge_base_sha: str
    indexed_head_sha: str | None
    index_covers_merge_base: bool
    branch_advanced_since_merge_base: bool


def resolve_pr_merge_base(
    repo_path: Path, *, base_branch: str, pr_head_sha: str, indexed_head_sha: str | None
) -> MergeBaseInfo:
    """Compute the merge base between a PR's head and its base branch, and
    report whether the currently-indexed snapshot of that base branch
    (`indexed_head_sha`, typically `BranchIndex.head_sha`) actually covers
    it.

    `index_covers_merge_base` is True when the merge-base commit is an
    ancestor of (or equal to) `indexed_head_sha` — i.e. the index was built
    from a point at or after where the PR branched off, so it has the
    context the PR's author actually saw. `branch_advanced_since_merge_base`
    is True when the index covers the merge base *and* has moved on since
    (the normal, expected case once `main` has taken further commits) — this
    is informational, not a problem by itself; a genuinely stale index is one
    where `index_covers_merge_base` is False, which means the stored
    snapshot predates the PR's fork point and cannot be used as-is.

    Deliberately just a function, not a service/endpoint — Stage 6 owns
    deciding what to do with this (walk the index backwards, or flag drift on
    the analysis run); Stage 5 only needs to expose the computation.
    """
    merge_base_sha = compute_merge_base(repo_path, base_branch, pr_head_sha)
    if indexed_head_sha is None:
        return MergeBaseInfo(
            merge_base_sha=merge_base_sha,
            indexed_head_sha=None,
            index_covers_merge_base=False,
            branch_advanced_since_merge_base=False,
        )

    covers = indexed_head_sha == merge_base_sha or is_ancestor(
        repo_path, merge_base_sha, indexed_head_sha
    )
    advanced = covers and indexed_head_sha != merge_base_sha
    return MergeBaseInfo(
        merge_base_sha=merge_base_sha,
        indexed_head_sha=indexed_head_sha,
        index_covers_merge_base=covers,
        branch_advanced_since_merge_base=advanced,
    )
