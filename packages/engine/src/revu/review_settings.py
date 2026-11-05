"""Per-repository review settings and the three things they control.

Set by an owner or admin on the repository's page and stored in
`repositories.config_json["review"]`:

* `ignore_globs`: files matching any pattern (e.g. `docs/**`, `*.snap`) are
  dropped from the diff before any model call, like lockfiles are.
* `max_tokens_per_review`: the per-review token ceiling. Before the model
  is called, the review's tokens are estimated from what will actually be
  sent (the diff after noise and ignore filtering, in the parts it will be
  reviewed in, plus the prompt and answer allowance per part). Above the
  ceiling the review stops and asks for confirmation instead of spending
  (Architecture §3.2-§3.3).
* `min_severity` and `max_comments`: what reaches the reader. Findings below
  the threshold are dropped, and the rest are capped, most severe and most
  confident first (§3.6: concise reviews are the ones acted on).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator

from revu.context.budget import count_tokens
from revu.context.chunks import DEFAULT_MAX_CHARS, split_for_review
from revu.context.diff import block_path, split_file_blocks
from revu.context.noise import drop_noise
from revu.models import Finding, Severity

__all__ = [
    "ANSWER_ALLOWANCE",
    "PROMPT_OVERHEAD",
    "IgnoredDiff",
    "OutputLimits",
    "ReviewSettings",
    "apply_output_limits",
    "drop_ignored",
    "estimate_review_tokens",
]

# Per part: the instructions and PR title/body around the diff, and the
# answer the model is allowed to write. Measured on revu's own prompt.
PROMPT_OVERHEAD = 1_200
ANSWER_ALLOWANCE = 2_000

_SEVERITY_RANK = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2, Severity.CRITICAL: 3}


class ReviewSettings(BaseModel):
    """A repository's review settings; every field has a safe default, so a
    repository nobody configured reviews exactly as before."""

    model_config = {"extra": "forbid"}

    ignore_globs: list[str] = Field(default_factory=list, max_length=50)
    min_severity: Severity = Severity.LOW
    max_comments: int | None = Field(default=None, ge=1, le=100)
    max_tokens_per_review: int | None = Field(default=None, ge=1_000, le=2_000_000)

    @field_validator("ignore_globs")
    @classmethod
    def _clean_globs(cls, globs: list[str]) -> list[str]:
        cleaned = []
        for glob in globs:
            glob = glob.strip()
            if not glob:
                continue
            if len(glob) > 200:
                raise ValueError("an ignore pattern can be at most 200 characters")
            cleaned.append(glob)
        return list(dict.fromkeys(cleaned))

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> ReviewSettings:
        """The settings stored on a repository; unreadable ones fall back to
        the defaults rather than blocking reviews."""
        raw = (config or {}).get("review")
        if not isinstance(raw, dict):
            return cls()
        try:
            return cls.model_validate(raw)
        except ValueError:
            return cls()


def _matches(path: str, glob: str) -> bool:
    # "docs/**" and "docs/*" both mean everything under docs/; a pattern
    # without a slash ("*.snap") matches the file name in any directory.
    if fnmatch.fnmatch(path, glob):
        return True
    if glob.endswith("/**") and path.startswith(glob[:-2]):
        return True
    if "/" not in glob:
        return fnmatch.fnmatch(path.rsplit("/", 1)[-1], glob)
    return False


@dataclass(frozen=True)
class IgnoredDiff:
    diff: str
    ignored: list[str] = field(default_factory=list)


def drop_ignored(diff_text: str, globs: list[str]) -> IgnoredDiff:
    """The diff without the files matching any of `globs`, and their paths."""
    if not globs:
        return IgnoredDiff(diff_text)
    kept: list[str] = []
    ignored: list[str] = []
    for block in split_file_blocks(diff_text):
        path = block_path(block)
        if path and any(_matches(path, glob) for glob in globs):
            ignored.append(path)
        else:
            kept.append(block)
    return IgnoredDiff("".join(kept), ignored)


def estimate_review_tokens(diff_text: str, *, max_part_chars: int = DEFAULT_MAX_CHARS) -> int:
    """Tokens the review will use, estimated before any model call: the diff
    the model will actually see, plus prompt and answer allowance per part.
    A cross-file review reads more context through its tools, so for it this
    is a lower bound."""
    filtered = drop_noise(diff_text).diff
    if not filtered.strip():
        return 0
    parts = split_for_review(filtered, max_part_chars)
    return sum(count_tokens(part) for part in parts) + len(parts) * (
        PROMPT_OVERHEAD + ANSWER_ALLOWANCE
    )


@dataclass(frozen=True)
class OutputLimits:
    findings: list[Finding]
    below_threshold: int = 0
    over_cap: int = 0


def apply_output_limits(findings: list[Finding], settings: ReviewSettings) -> OutputLimits:
    """Drop findings below `min_severity`, then keep at most `max_comments`,
    most severe and most confident first."""
    floor = _SEVERITY_RANK[settings.min_severity]
    kept = [f for f in findings if _SEVERITY_RANK[Severity(f.severity)] >= floor]
    below = len(findings) - len(kept)
    if settings.max_comments is None or len(kept) <= settings.max_comments:
        return OutputLimits(kept, below_threshold=below)
    ranked = sorted(
        kept, key=lambda f: (_SEVERITY_RANK[Severity(f.severity)], f.confidence), reverse=True
    )
    return OutputLimits(
        ranked[: settings.max_comments],
        below_threshold=below,
        over_cap=len(kept) - settings.max_comments,
    )
