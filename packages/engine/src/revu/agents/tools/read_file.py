"""`read_file` tool: read a file's content, optionally restricted to a line
range, from a repository working tree.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class ReadFileResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    file_path: str
    found: bool
    content: str = ""
    line_start: int = 1
    line_end: int = 0
    total_lines: int = 0
    error: str | None = None


def read_file(
    repo_root: Path,
    file_path: str,
    *,
    line_start: int | None = None,
    line_end: int | None = None,
) -> ReadFileResult:
    """Read `file_path` (relative to `repo_root`) as text, optionally
    restricted to the inclusive 1-indexed `[line_start, line_end]` range.

    Never raises — a missing file, a path escaping `repo_root`, or a range
    outside the file's actual line count all come back as a structured
    `ReadFileResult` with `found=False`/`error` set. A tool an agent calls
    with arguments it invented is not something a malformed argument should
    be able to crash.
    """
    root = repo_root.resolve()
    resolved = (root / file_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return ReadFileResult(file_path=file_path, found=False, error="path escapes repo_root")

    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ReadFileResult(file_path=file_path, found=False, error=str(exc))

    lines = text.splitlines()
    total = len(lines)

    start = max(line_start or 1, 1)
    end = min(line_end if line_end is not None else total, total)
    if total == 0 or end < start:
        return ReadFileResult(
            file_path=file_path,
            found=True,
            total_lines=total,
            line_start=start,
            line_end=start - 1,
            content="",
        )

    content = "\n".join(lines[start - 1 : end])
    return ReadFileResult(
        file_path=file_path,
        found=True,
        content=content,
        line_start=start,
        line_end=end,
        total_lines=total,
    )
