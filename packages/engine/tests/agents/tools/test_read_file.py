from pathlib import Path

from revu.agents.tools.read_file import read_file


def _write(root: Path, rel_path: str, content: str) -> None:
    p = root / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_reads_whole_file_by_default(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "line1\nline2\nline3\n")
    result = read_file(tmp_path, "a.py")
    assert result.found is True
    assert result.content == "line1\nline2\nline3"
    assert result.total_lines == 3
    assert (result.line_start, result.line_end) == (1, 3)


def test_reads_a_line_range(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "line1\nline2\nline3\nline4\n")
    result = read_file(tmp_path, "a.py", line_start=2, line_end=3)
    assert result.content == "line2\nline3"
    assert (result.line_start, result.line_end) == (2, 3)


def test_line_end_beyond_file_is_clamped(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "line1\nline2\n")
    result = read_file(tmp_path, "a.py", line_start=1, line_end=1000)
    assert result.content == "line1\nline2"
    assert result.line_end == 2


def test_missing_file_returns_structured_not_found(tmp_path: Path) -> None:
    result = read_file(tmp_path, "does_not_exist.py")
    assert result.found is False
    assert result.error is not None


def test_path_escaping_repo_root_is_rejected(tmp_path: Path) -> None:
    result = read_file(tmp_path, "../../etc/passwd")
    assert result.found is False
    assert result.error == "path escapes repo_root"


def test_empty_file(tmp_path: Path) -> None:
    _write(tmp_path, "empty.py", "")
    result = read_file(tmp_path, "empty.py")
    assert result.found is True
    assert result.total_lines == 0
    assert result.content == ""


def test_line_start_after_file_end_returns_empty_content(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "line1\nline2\n")
    result = read_file(tmp_path, "a.py", line_start=10)
    assert result.found is True
    assert result.content == ""
