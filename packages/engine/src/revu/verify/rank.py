"""Apply a confidence threshold, then sort and cap the surviving findings.

Per the architecture doc's own justification for a comment cap: "concise,
hunk-level, snippet-rich comments are the ones that actually cause code
changes" - a long tail of low-confidence findings dilutes a PR review rather
than improving it, so this is the final, deliberately blunt filter in the
pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

from revu.models import Finding, Severity

DEFAULT_THRESHOLD = 0.5
DEFAULT_MAX_FINDINGS = 20

_SEVERITY_RANK: dict[Severity, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


@dataclass(frozen=True)
class RankResult:
    findings: list[Finding]
    total_in: int
    dropped_below_threshold: int
    cut_by_cap: int


def rank_findings(
    findings: list[Finding],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    max_findings: int = DEFAULT_MAX_FINDINGS,
) -> RankResult:
    """Drop findings with `confidence < threshold`, sort the rest by
    (severity descending, confidence descending), then cap at
    `max_findings`.

    Sorting by severity *before* confidence means the cap can never discard
    a critical finding to make room for a merely-confident low-severity one
    - severity is checked first, confidence only breaks ties within the
    same severity.
    """
    total_in = len(findings)
    passed = [f for f in findings if f.confidence >= threshold]
    dropped_below_threshold = total_in - len(passed)

    ordered = sorted(passed, key=lambda f: (-_SEVERITY_RANK[f.severity], -f.confidence))
    kept = ordered[:max_findings]
    cut_by_cap = len(ordered) - len(kept)

    return RankResult(
        findings=kept,
        total_in=total_in,
        dropped_below_threshold=dropped_below_threshold,
        cut_by_cap=cut_by_cap,
    )


__all__ = ["RankResult", "rank_findings", "DEFAULT_THRESHOLD", "DEFAULT_MAX_FINDINGS"]
