"""Walk a checked-out repository (or any directory) for Python source files.

Two paths: if the root looks like a git working tree, `git ls-files` is used
directly (tracked files only, so `.gitignore`/`.git/info/exclude` are
respected for free and we never accidentally index build artefacts that
happen to sit in a tracked-looking directory). Otherwise a plain filesystem
walk is used with a hard-coded vendored/binary directory skip-list and a
minimal, root-level-only `.gitignore` matcher — good enough for indexing
arbitrary non-git directories (e.g. a benchmark corpus under
`site-packages`), not a full gitignore implementation.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from git import GitCommandError, InvalidGitRepositoryError, Repo

DEFAULT_MAX_FILE_SIZE = 1_000_000  # ~1MB; generated/vendored files can be huge for no benefit

EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".next",
    "dist",
    "build",
    ".eggs",
    ".idea",
    ".vscode",
}


def _is_excluded_dir(name: str) -> bool:
    return name in EXCLUDED_DIRS or name.endswith(".egg-info")


def _load_root_gitignore(root: Path) -> list[str]:
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return []
    patterns = []
    for line in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line.rstrip("/"))
    return patterns


def _matches_gitignore(relative_posix: str, patterns: list[str]) -> bool:
    parts = relative_posix.split("/")
    return any(
        fnmatch.fnmatch(relative_posix, pattern) or any(fnmatch.fnmatch(p, pattern) for p in parts)
        for pattern in patterns
    )


def _iter_via_git_ls_files(root: Path) -> list[Path] | None:
    try:
        repo = Repo(root, search_parent_directories=False)
    except InvalidGitRepositoryError:
        return None
    try:
        output = repo.git.ls_files("--", "*.py")
    except GitCommandError:
        return None
    return [root / line for line in output.splitlines() if line]


def _iter_via_filesystem_walk(root: Path) -> list[Path]:
    patterns = _load_root_gitignore(root)
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _is_excluded_dir(d)]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            full_path = Path(dirpath) / filename
            relative_posix = full_path.relative_to(root).as_posix()
            if patterns and _matches_gitignore(relative_posix, patterns):
                continue
            found.append(full_path)
    return found


def iter_python_files(
    root: Path, *, max_file_size: int = DEFAULT_MAX_FILE_SIZE
) -> list[Path]:
    """Return all `.py` files under `root` worth indexing, as absolute paths.

    Files over `max_file_size` bytes are skipped silently — they're
    overwhelmingly generated code (parser tables, vendored bundles) with a
    poor cost/benefit for symbol extraction.
    """
    candidates = _iter_via_git_ls_files(root)
    if candidates is None:
        candidates = _iter_via_filesystem_walk(root)

    result = []
    for path in candidates:
        try:
            if not path.is_file():
                continue
            if path.stat().st_size > max_file_size:
                continue
        except OSError:
            continue
        result.append(path)
    return result
