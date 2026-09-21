from pathlib import Path

from git import Repo
from revu.index.walker import DEFAULT_MAX_FILE_SIZE, iter_python_files


def _relative(root: Path, paths: list[Path]) -> set[str]:
    return {p.relative_to(root).as_posix() for p in paths}


def test_filesystem_walk_finds_py_files_and_skips_excluded_dirs(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1\n")
    (tmp_path / "pkg" / "__pycache__").mkdir()
    (tmp_path / "pkg" / "__pycache__" / "mod.cpython-312.pyc.py").write_text("junk")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "vendored.py").write_text("junk")
    (tmp_path / "not_python.txt").write_text("nope")

    found = iter_python_files(tmp_path)
    assert _relative(tmp_path, found) == {"pkg/mod.py"}


def test_filesystem_walk_respects_root_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored_dir\ngenerated.py\n")
    (tmp_path / "kept.py").write_text("x = 1\n")
    (tmp_path / "generated.py").write_text("x = 1\n")
    (tmp_path / "ignored_dir").mkdir()
    (tmp_path / "ignored_dir" / "also_kept_out.py").write_text("x = 1\n")

    found = iter_python_files(tmp_path)
    assert _relative(tmp_path, found) == {"kept.py"}


def test_filesystem_walk_skips_files_over_max_size(tmp_path: Path) -> None:
    small = tmp_path / "small.py"
    small.write_text("x = 1\n")
    big = tmp_path / "big.py"
    big.write_text("x = 1\n" * 100)

    found = iter_python_files(tmp_path, max_file_size=50)
    assert _relative(tmp_path, found) == {"small.py"}


def test_git_repo_uses_ls_files_and_respects_gitignore(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test User")
        cfg.set_value("user", "email", "test@example.com")

    (repo_dir / ".gitignore").write_text("untracked_ignored.py\n")
    (repo_dir / "tracked.py").write_text("x = 1\n")
    (repo_dir / "untracked_ignored.py").write_text("x = 1\n")  # never added, and ignored
    (repo_dir / "untracked_not_ignored.py").write_text("x = 1\n")  # never added, not ignored either

    repo.index.add(["tracked.py", ".gitignore"])
    repo.index.commit("initial")

    found = iter_python_files(repo_dir)
    # git ls-files only returns tracked files, so both untracked files are
    # absent regardless of whether they're gitignored.
    assert _relative(repo_dir, found) == {"tracked.py"}


def test_default_max_file_size_is_about_one_megabyte() -> None:
    assert DEFAULT_MAX_FILE_SIZE == 1_000_000
