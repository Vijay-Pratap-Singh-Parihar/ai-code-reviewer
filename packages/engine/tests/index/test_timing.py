"""Timing gate from the roadmap: cold build < 60s, incremental update < 5s,
for a ~50k LOC repo.

There's no 50k LOC repo bundled with this project, so this uses the
installed `pydantic` package (~46k LOC across ~105 files as of the version
pinned in `uv.lock`) as a real, substantial, non-trivial Python codebase —
close enough to the target size to be a meaningful check, and available in
every environment that has already run `uv sync` (no network access or
extra fixtures needed). If `pydantic`'s installed size drifts far from
~50k LOC in a future dependency bump, the printed LOC count in the failure
message will make that obvious.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pydantic
import pytest
from git import Repo
from revu.index.graph import build_index_at_path
from revu.index.incremental import incremental_update

COLD_BUILD_BUDGET_SECONDS = 60
INCREMENTAL_BUDGET_SECONDS = 5


def _pydantic_source_dir() -> Path:
    return Path(pydantic.__file__).resolve().parent


def _loc_count(root: Path) -> int:
    total = 0
    for path in root.rglob("*.py"):
        try:
            total += sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return total


def test_cold_build_under_60_seconds_for_a_real_50k_loc_ish_corpus() -> None:
    source_dir = _pydantic_source_dir()
    loc = _loc_count(source_dir)

    start = time.monotonic()
    result = build_index_at_path(source_dir)
    elapsed = time.monotonic() - start

    print(
        f"\n[timing] cold build: {loc} LOC, {result.files_indexed} files, "
        f"{result.node_count} nodes, {result.edge_count} edges, {elapsed:.2f}s"
    )
    assert result.files_indexed > 0
    assert elapsed < COLD_BUILD_BUDGET_SECONDS, (
        f"cold build took {elapsed:.2f}s for {loc} LOC (budget: "
        f"{COLD_BUILD_BUDGET_SECONDS}s for ~50k LOC)"
    )


@pytest.fixture
def pydantic_git_repo(tmp_path: Path) -> tuple[Path, str]:
    """A real git repo seeded with the installed pydantic source, so
    `incremental_update` can run against something the size of a genuine
    ~50k LOC codebase rather than a handful of synthetic files.
    """
    repo_dir = tmp_path / "pydantic_copy"
    shutil.copytree(
        _pydantic_source_dir(), repo_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )

    repo = Repo.init(repo_dir)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test User")
        cfg.set_value("user", "email", "test@example.com")
    repo.git.add(A=True)
    old_sha = repo.index.commit("initial import of pydantic source").hexsha
    return repo_dir, old_sha


def test_incremental_update_under_5_seconds_after_one_file_changes(
    pydantic_git_repo: tuple[Path, str],
) -> None:
    repo_dir, old_sha = pydantic_git_repo
    repo = Repo(repo_dir)

    full_build_result = build_index_at_path(repo_dir, commit_sha=old_sha)

    # Touch exactly one real, non-trivial file — main.py is pydantic's core
    # BaseModel definition, present in every installed version.
    target_file = repo_dir / "main.py"
    with target_file.open("a", encoding="utf-8") as f:
        f.write("\n\ndef _revu_incremental_timing_probe():\n    return 1\n")
    repo.git.add(A=True)
    new_sha = repo.index.commit("touch one file").hexsha

    start = time.monotonic()
    incremental_result = incremental_update(repo_dir, old_sha, new_sha, full_build_result)
    elapsed = time.monotonic() - start

    print(
        f"\n[timing] incremental update: {incremental_result.files_reparsed} file(s) reparsed "
        f"out of {full_build_result.files_indexed}, {elapsed:.2f}s"
    )
    assert incremental_result.files_reparsed == 1
    assert "main._revu_incremental_timing_probe" in {
        s.qualified_name for s in incremental_result.index.symbols
    }
    assert elapsed < INCREMENTAL_BUDGET_SECONDS, (
        f"incremental update took {elapsed:.2f}s for a 1-file change (budget: "
        f"{INCREMENTAL_BUDGET_SECONDS}s)"
    )
