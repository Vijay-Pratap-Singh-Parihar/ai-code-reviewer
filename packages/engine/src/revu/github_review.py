"""Turning a run's findings into one GitHub pull request review.

GitHub only accepts an inline review comment on a line that appears in the
pull request's diff on the new-file side (an added or context line inside a
hunk). A single comment outside that set makes GitHub reject the *whole*
review with 422, so every finding is placed before posting:

- its cited lines are inside one hunk of a changed file: an inline comment
  on the RIGHT side (multi-line when the range is commentable end to end);
- otherwise (an unchanged file, a line outside the hunks, a deleted file):
  listed in the review's summary body, so nothing is lost.

The review is posted as `COMMENT`: revu never approves or blocks a pull
request; people decide. Comments are capped (`MAX_INLINE_COMMENTS`) so one
noisy run can't bury a PR; the overflow goes to the summary too.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from revu.context.diff import parse_diff

MAX_INLINE_COMMENTS = 30
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@dataclass(frozen=True)
class ReviewEvidence:
    path: str
    line_start: int
    line_end: int
    reason: str


@dataclass(frozen=True)
class ReviewFinding:
    path: str
    line_start: int
    line_end: int
    severity: str
    category: str
    message: str
    confidence: float | None = None
    evidence: tuple[ReviewEvidence, ...] = ()


@dataclass
class ReviewPayload:
    """The body of `POST /repos/{owner}/{repo}/pulls/{number}/reviews`."""

    body: str
    comments: list[dict[str, Any]] = field(default_factory=list)
    inline: int = 0
    in_summary: int = 0

    def to_request(self, commit_id: str) -> dict[str, Any]:
        return {
            "commit_id": commit_id,
            "event": "COMMENT",
            "body": self.body,
            "comments": self.comments,
        }


def commentable_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """For each file still present after the change, the new-file line
    ranges GitHub accepts comments on: one contiguous range per hunk."""
    ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for file_diff in parse_diff(diff_text):
        if file_diff.is_deletion:
            continue
        for hunk in file_diff.hunks:
            lines = [ln.new_lineno for ln in hunk.lines if ln.new_lineno is not None]
            if lines:
                ranges[file_diff.file_path].append((min(lines), max(lines)))
    return dict(ranges)


def _placement(
    finding: ReviewFinding, ranges: dict[str, list[tuple[int, int]]]
) -> tuple[int, int] | None:
    """(start_line, line) for an inline comment, or None if it can't be
    inline. Both ends must sit in the same hunk; if only the last line is
    commentable, comment on that line alone."""
    end = max(finding.line_start, finding.line_end)
    start = min(finding.line_start, finding.line_end)
    for low, high in ranges.get(finding.path.lstrip("/"), []):
        if low <= end <= high:
            return (start if start >= low else end, end)
    return None


def _severity_rank(finding: ReviewFinding) -> tuple[int, str, int]:
    return (_SEVERITY_ORDER.get(finding.severity.lower(), 9), finding.path, finding.line_start)


def _label(finding: ReviewFinding) -> str:
    return f"**{finding.severity.capitalize()} · {finding.category.replace('_', ' ')}**"


def _location(path: str, start: int, end: int) -> str:
    return f"`{path}:{start}`" if start == end else f"`{path}:{start}-{end}`"


def comment_body(finding: ReviewFinding) -> str:
    parts = [_label(finding), "", finding.message.strip()]
    if finding.evidence:
        parts += ["", "<details><summary>Evidence</summary>", ""]
        parts += [
            f"- {_location(e.path, e.line_start, e.line_end)}: {e.reason.strip()}"
            for e in finding.evidence
        ]
        parts += ["", "</details>"]
    footer = "revu"
    if finding.confidence is not None:
        footer += f" · confidence {finding.confidence:.2f}"
    parts += ["", f"<sub>{footer}</sub>"]
    return "\n".join(parts)


def build_review(
    findings: Sequence[ReviewFinding],
    diff_text: str,
    *,
    model_label: str | None = None,
    max_inline: int = MAX_INLINE_COMMENTS,
) -> ReviewPayload:
    ranges = commentable_ranges(diff_text)
    comments: list[dict[str, Any]] = []
    outside: list[ReviewFinding] = []
    for finding in sorted(findings, key=_severity_rank):
        placement = _placement(finding, ranges)
        if placement is None or len(comments) >= max_inline:
            outside.append(finding)
            continue
        start, line = placement
        comment: dict[str, Any] = {
            "path": finding.path.lstrip("/"),
            "line": line,
            "side": "RIGHT",
            "body": comment_body(finding),
        }
        if start != line:
            comment |= {"start_line": start, "start_side": "RIGHT"}
        comments.append(comment)

    total = len(findings)
    if total == 0:
        headline = "revu found no issues in this change."
    else:
        headline = (
            f"revu found {total} {'issue' if total == 1 else 'issues'}: "
            f"{len(comments)} commented inline"
            + (f", {len(outside)} listed below" if outside else "")
            + "."
        )
    parts = ["## revu review", "", headline]
    if outside:
        parts += [
            "",
            "### Not on the changed lines",
            "",
            "GitHub only accepts comments on lines in this pull request's diff, so these are "
            "listed here:",
            "",
        ]
        parts += [
            f"- {_label(f)} {_location(f.path, f.line_start, f.line_end)}: {f.message.strip()}"
            for f in outside
        ]
    footer = "Posted by revu as a comment: it never approves or blocks a pull request."
    if model_label:
        footer += f" Reviewed with {model_label}."
    parts += ["", f"<sub>{footer}</sub>"]
    return ReviewPayload(
        body="\n".join(parts), comments=comments, inline=len(comments), in_summary=len(outside)
    )


def summary_only(findings: Sequence[ReviewFinding], **kwargs: Any) -> ReviewPayload:
    """The same review with every finding in the body: the fallback when
    GitHub rejects the inline comments (e.g. the PR moved since review)."""
    return build_review(findings, "", **kwargs)
