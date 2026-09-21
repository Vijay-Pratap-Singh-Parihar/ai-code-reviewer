"""Parse a unified diff string into per-file hunks.

Hand-rolled rather than pulling in a diff-parsing library: the format this
needs to handle (`git diff`/unified diff, possibly with the `diff --git`
extended header block, possibly without it) is small and fixed, and the
line-number bookkeeping is exactly the kind of thing worth pinning with
direct unit tests rather than trusting to a dependency's own semantics.

Only the pieces `context/mapping.py` and `context/rank.py` need are modelled:
per-hunk old/new line ranges and, for every line in a hunk, whether it was
added, removed, or unchanged context, plus its line number(s) in the old
and/or new file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class LineKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    CONTEXT = "context"


@dataclass(frozen=True)
class DiffLine:
    """One line inside a hunk body."""

    kind: LineKind
    old_lineno: int | None
    new_lineno: int | None
    content: str


@dataclass(frozen=True)
class Hunk:
    """One `@@ -a,b +c,d @@` block and its body lines."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[DiffLine, ...]

    def new_line_range(self) -> tuple[int, int]:
        """Inclusive (start, end) new-file line range this hunk touches,
        covering both context and added lines (i.e. every line that still
        exists in the new file within this hunk).

        A pure-deletion hunk (`new_count == 0`, no context lines at all —
        only possible with a zero-context diff) carries no new-file lines of
        its own; falls back to a single-point range at `new_start`, which is
        the insertion point the deletion happened at.
        """
        new_linenos = [ln.new_lineno for ln in self.lines if ln.new_lineno is not None]
        if not new_linenos:
            point = max(self.new_start, 1)
            return (point, point)
        return (min(new_linenos), max(new_linenos))

    def changed_new_line_range(self) -> tuple[int, int] | None:
        """Inclusive (start, end) new-file line range covering only the
        *added* lines (not surrounding context). `None` for a pure-deletion
        hunk, which adds nothing to the new file.
        """
        added = [
            ln.new_lineno
            for ln in self.lines
            if ln.kind is LineKind.ADDED and ln.new_lineno is not None
        ]
        if not added:
            return None
        return (min(added), max(added))

    def removed_old_line_range(self) -> tuple[int, int] | None:
        """Inclusive (start, end) old-file line range covering only the
        *removed* lines. `None` for a pure-addition hunk.
        """
        removed = [
            ln.old_lineno
            for ln in self.lines
            if ln.kind is LineKind.REMOVED and ln.old_lineno is not None
        ]
        if not removed:
            return None
        return (min(removed), max(removed))


@dataclass(frozen=True)
class FileDiff:
    """All hunks touching one file."""

    old_path: str | None
    new_path: str | None
    hunks: tuple[Hunk, ...]

    @property
    def file_path(self) -> str:
        """The path to key graph/file lookups on: the new path, unless this
        is a pure deletion (no new path — the file no longer exists), in
        which case the old path is all that ever existed.
        """
        return self.new_path or self.old_path or ""

    @property
    def is_deletion(self) -> bool:
        return self.new_path is None

    @property
    def is_addition(self) -> bool:
        return self.old_path is None


def _strip_prefix(path: str) -> str | None:
    """Strip a leading `a/`/`b/` prefix (`git diff`'s default) and normalise
    `/dev/null` (used for a pure addition's `---` line or a pure deletion's
    `+++` line) to `None`.
    """
    path = path.strip()
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _parse_hunk(lines: list[str], index: int) -> tuple[Hunk, int]:
    match = _HUNK_HEADER_RE.match(lines[index])
    if match is None:  # pragma: no cover - only called after confirming a match
        raise ValueError(f"not a hunk header: {lines[index]!r}")

    old_start = int(match.group(1))
    old_count = int(match.group(2)) if match.group(2) is not None else 1
    new_start = int(match.group(3))
    new_count = int(match.group(4)) if match.group(4) is not None else 1

    old_lineno = old_start
    new_lineno = new_start
    body: list[DiffLine] = []

    i = index + 1
    while i < len(lines):
        line = lines[i]
        if line.startswith("\\"):
            # "\ No newline at end of file" - not a content line.
            i += 1
            continue
        if not line:
            break
        prefix, content = line[0], line[1:]
        if prefix == " ":
            body.append(
                DiffLine(
                    kind=LineKind.CONTEXT, old_lineno=old_lineno, new_lineno=new_lineno,
                    content=content,
                )
            )
            old_lineno += 1
            new_lineno += 1
        elif prefix == "+":
            body.append(
                DiffLine(
                    kind=LineKind.ADDED, old_lineno=None, new_lineno=new_lineno, content=content
                )
            )
            new_lineno += 1
        elif prefix == "-":
            body.append(
                DiffLine(
                    kind=LineKind.REMOVED, old_lineno=old_lineno, new_lineno=None, content=content
                )
            )
            old_lineno += 1
        else:
            break
        i += 1

    return Hunk(
        old_start=old_start, old_count=old_count, new_start=new_start, new_count=new_count,
        lines=tuple(body),
    ), i


def parse_diff(diff_text: str) -> list[FileDiff]:
    """Parse a unified diff (with or without `diff --git` extended headers)
    into one `FileDiff` per file, each with every hunk it contains.

    Handles: multiple hunks per file, multiple files per diff, pure-addition
    hunks (no removed lines), pure-removal hunks (no added lines), and a
    trailing `\\ No newline at end of file` marker (ignored, not counted as
    content).
    """
    lines = diff_text.splitlines()
    file_diffs: list[FileDiff] = []

    old_path: str | None = None
    new_path: str | None = None
    hunks: list[Hunk] = []
    have_file = False

    def flush() -> None:
        nonlocal old_path, new_path, hunks, have_file
        if have_file:
            file_diffs.append(FileDiff(old_path=old_path, new_path=new_path, hunks=tuple(hunks)))
        old_path, new_path, hunks, have_file = None, None, [], False

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            flush()
            i += 1
            continue
        if line.startswith("--- "):
            old_path = _strip_prefix(line[4:])
            have_file = True
            i += 1
            continue
        if line.startswith("+++ "):
            new_path = _strip_prefix(line[4:])
            have_file = True
            i += 1
            continue
        if _HUNK_HEADER_RE.match(line):
            hunk, i = _parse_hunk(lines, i)
            hunks.append(hunk)
            have_file = True
            continue
        i += 1

    flush()
    return file_diffs
