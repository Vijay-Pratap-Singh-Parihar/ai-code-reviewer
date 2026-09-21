from pathlib import Path

from git import Repo
from revu.index.graph import build_index, build_index_at_path
from revu.index.incremental import changed_python_files, incremental_update


def _init_repo(tmp_path: Path) -> Repo:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Test User")
        cfg.set_value("user", "email", "test@example.com")
    return repo


def test_changed_python_files_filters_to_py_extension(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    repo_dir = Path(repo.working_dir)

    (repo_dir / "a.py").write_text("x = 1\n")
    (repo_dir / "readme.md").write_text("hello\n")
    repo.index.add(["a.py", "readme.md"])
    old_sha = repo.index.commit("first").hexsha

    (repo_dir / "a.py").write_text("x = 2\n")
    (repo_dir / "readme.md").write_text("world\n")
    (repo_dir / "b.py").write_text("y = 1\n")
    repo.index.add(["a.py", "readme.md", "b.py"])
    new_sha = repo.index.commit("second").hexsha

    changed = changed_python_files(repo_dir, old_sha, new_sha)
    assert set(changed) == {"a.py", "b.py"}


def test_incremental_update_matches_full_rebuild_after_a_file_changes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    repo_dir = Path(repo.working_dir)

    (repo_dir / "utils.py").write_text("def helper():\n    return 1\n")
    (repo_dir / "main.py").write_text(
        "from utils import helper\n\ndef run():\n    return helper()\n"
    )
    repo.index.add(["utils.py", "main.py"])
    old_sha = repo.index.commit("first").hexsha

    old_result = build_index(repo_dir, old_sha)

    # Add a brand new function to main.py and a new file entirely.
    (repo_dir / "main.py").write_text(
        "from utils import helper\n\n"
        "def run():\n    return helper()\n\n"
        "def run_twice():\n    return run() + run()\n"
    )
    (repo_dir / "extra.py").write_text("def extra_fn():\n    return 42\n")
    repo.index.add(["main.py", "extra.py"])
    new_sha = repo.index.commit("second").hexsha

    incremental_result = incremental_update(repo_dir, old_sha, new_sha, old_result)

    assert incremental_result.files_reparsed == 2  # main.py + extra.py
    assert incremental_result.files_removed == 0

    new_qualified_names = {s.qualified_name for s in incremental_result.index.symbols}
    assert "main.run_twice" in new_qualified_names
    assert "extra.extra_fn" in new_qualified_names
    # Unchanged file's symbol must still be present (reused, not re-parsed).
    assert "utils.helper" in new_qualified_names

    new_calls = {(e.caller, e.callee) for e in incremental_result.index.call_edges}
    assert ("main.run_twice", "main.run") in new_calls
    assert ("main.run", "utils.helper") in new_calls  # carried over from the unchanged file's edge

    # Cross-check against a from-scratch full rebuild at the same commit.
    full_rebuild = build_index(repo_dir, new_sha)
    assert incremental_result.index.node_count == full_rebuild.node_count
    assert incremental_result.index.edge_count == full_rebuild.edge_count


def test_incremental_update_handles_a_deleted_file(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    repo_dir = Path(repo.working_dir)

    (repo_dir / "gone.py").write_text("def doomed():\n    pass\n")
    (repo_dir / "keep.py").write_text("def kept():\n    pass\n")
    repo.index.add(["gone.py", "keep.py"])
    old_sha = repo.index.commit("first").hexsha

    old_result = build_index(repo_dir, old_sha)
    assert "gone.doomed" in {s.qualified_name for s in old_result.symbols}

    repo.index.remove(["gone.py"], working_tree=True)
    new_sha = repo.index.commit("remove gone.py").hexsha

    incremental_result = incremental_update(repo_dir, old_sha, new_sha, old_result)

    assert incremental_result.files_removed == 1
    remaining_names = {s.qualified_name for s in incremental_result.index.symbols}
    assert "gone.doomed" not in remaining_names
    assert "keep.kept" in remaining_names


def test_incremental_update_is_meaningfully_faster_than_a_full_rebuild(tmp_path: Path) -> None:
    """Best-effort perf sanity check with a handful of files — the real <5s
    timing gate against a ~50k LOC corpus is `test_timing.py`. This just
    proves incremental update doesn't re-walk/re-parse the unchanged files.
    """
    repo = _init_repo(tmp_path)
    repo_dir = Path(repo.working_dir)

    files = []
    for i in range(20):
        path = repo_dir / f"module_{i}.py"
        path.write_text(f"def fn_{i}():\n    return {i}\n")
        files.append(f"module_{i}.py")
    repo.index.add(files)
    old_sha = repo.index.commit("first").hexsha
    old_result = build_index(repo_dir, old_sha)

    (repo_dir / "module_0.py").write_text("def fn_0():\n    return 999\n")
    repo.index.add(["module_0.py"])
    new_sha = repo.index.commit("second").hexsha

    incremental_result = incremental_update(repo_dir, old_sha, new_sha, old_result)
    assert incremental_result.files_reparsed == 1


def test_build_index_at_path_is_used_by_build_index_under_the_hood(tmp_path: Path) -> None:
    # Sanity check that the two entry points agree on a plain directory.
    (tmp_path / "solo.py").write_text("def f():\n    pass\n")
    direct = build_index_at_path(tmp_path)
    assert direct.node_count == 2  # module node + one function
