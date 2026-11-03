"""Split a large diff into parts a model can review one at a time.

A 50k-character diff in one prompt is slow on a local model (prompt
processing grows faster than linearly), risks the request timing out, and
reviews worse: findings concentrate on the first files. Parts are whole
files packed together up to a size limit; a file larger than the limit is
cut between hunks, each piece keeping the file's header so line numbers
stay right.
"""

from __future__ import annotations

from revu.context.diff import split_file_blocks

# About 7k tokens of code: comfortably inside a local model's context next
# to the prompt and the answer, and quick enough to stay under the timeout.
DEFAULT_MAX_CHARS = 24_000


def split_for_review(diff_text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """Parts of `diff_text`, each at most `max_chars` unless a single hunk is
    larger on its own (it is never cut). A small diff is one part."""
    if len(diff_text) <= max_chars:
        return [diff_text] if diff_text.strip() else []
    pieces: list[str] = []
    for block in split_file_blocks(diff_text):
        pieces.extend(_split_block(block, max_chars) if len(block) > max_chars else [block])
    parts: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > max_chars:
            parts.append(current)
            current = ""
        current += piece
    if current:
        parts.append(current)
    return parts


def _split_block(block: str, max_chars: int) -> list[str]:
    """One file's diff cut between hunks, the header repeated on each piece."""
    lines = block.splitlines(keepends=True)
    first_hunk = next((i for i, line in enumerate(lines) if line.startswith("@@ ")), None)
    if first_hunk is None:
        return [block]
    header = "".join(lines[:first_hunk])
    hunks: list[str] = []
    for line in lines[first_hunk:]:
        if line.startswith("@@ ") or not hunks:
            hunks.append("")
        hunks[-1] += line
    pieces: list[str] = []
    current = ""
    for hunk in hunks:
        if current and len(header) + len(current) + len(hunk) > max_chars:
            pieces.append(header + current)
            current = ""
        current += hunk
    pieces.append(header + current)
    return pieces
