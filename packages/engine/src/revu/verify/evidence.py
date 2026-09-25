"""Resolve every finding's own location and every item in its evidence list
against the real repository on disk - the roadmap's own framing for why this
matters: "log the drop rate - it measures hallucination directly."

**Reuse, not reimplementation.** `revu.agents.tools.read_file` already has a
tested, safe "read a real file's content" implementation (path-escape
checks via `resolve()`/`relative_to()`, `OSError` handling for a missing
file). Reimplementing those safety checks here would be exactly the
duplication `IMPLEMENTATION_PLAN.md`'s Stage 7 notes warn against
(`find_callers` reusing Stage 6's `bounded_expand` is the precedent). This
module calls `read_file` and adds exactly one thing `read_file` itself
doesn't do: `read_file` *clamps* an out-of-range `line_end` down to the
file's actual last line (a lenient tool-call contract, correct for an agent
reading code) rather than reporting a failure - but a **finding claiming
evidence at a line number past the end of the file is exactly the
hallucination case this module exists to catch**, so it treats a requested
range that exceeds `total_lines` as unresolved.

**Drop-by-default semantics.** A finding is dropped (`mode="drop"`, the
default) if *either* its own location or *any* one of its evidence items
fails to resolve - not just its own location. This is the stricter, more
defensible reading of the roadmap's "drop unresolvable findings": a finding
that cites even one fabricated piece of evidence has already demonstrated
its citations can't be trusted, so the whole finding (not just the bad
evidence item) is treated as suspect. `mode="flag"` never drops or mutates
anything - it exists purely so a caller can inspect the resolution outcome
(via `EvidenceResolutionResult.reports`) without losing data, e.g. for
manual review or debugging a model's citation behaviour; the live pipeline
(`verify.verify_findings`) uses `"drop"`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from revu.agents.tools.read_file import read_file
from revu.models import Finding


@dataclass(frozen=True)
class LocationResolution:
    """The outcome of checking one (file_path, line_start, line_end) triple
    against the real repository.
    """

    file_path: str
    line_start: int
    line_end: int
    resolved: bool
    reason: str | None = None


def resolve_location(
    repo_root: Path, file_path: str, line_start: int, line_end: int
) -> LocationResolution:
    """Does `file_path` exist under `repo_root`, and is `[line_start,
    line_end]` within that file's actual line count?
    """
    if line_start < 1 or line_end < line_start:
        return LocationResolution(
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            resolved=False,
            reason=f"invalid line range [{line_start}, {line_end}]",
        )

    result = read_file(repo_root, file_path, line_start=line_start, line_end=line_end)
    if not result.found:
        return LocationResolution(
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            resolved=False,
            reason=result.error or "file not found",
        )

    if line_end > result.total_lines:
        return LocationResolution(
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            resolved=False,
            reason=f"line {line_end} is beyond {file_path}'s actual {result.total_lines} lines",
        )

    return LocationResolution(
        file_path=file_path, line_start=line_start, line_end=line_end, resolved=True
    )


@dataclass(frozen=True)
class FindingEvidenceReport:
    """The full resolution outcome for one finding: its own location, every
    evidence item's location, and the decision (`kept`) that followed from
    them under whatever `mode` `resolve_evidence` was called with.
    """

    original_finding: Finding
    own_location: LocationResolution
    evidence_resolutions: tuple[LocationResolution, ...]
    dropped_evidence_count: int
    total_evidence_count: int
    all_resolved: bool
    resulting_finding: Finding | None


@dataclass(frozen=True)
class EvidenceResolutionResult:
    """`drop_rate` is the fraction of *input findings* dropped for citing an
    unresolvable location - this is the number the roadmap says "measures
    hallucination directly," returned as real data (not just logged) so a
    caller/UI can report it.
    """

    reports: tuple[FindingEvidenceReport, ...]
    survived: list[Finding]
    dropped_count: int
    total_count: int
    drop_rate: float


def resolve_evidence(
    findings: list[Finding],
    *,
    repo_root: Path,
    mode: Literal["drop", "flag"] = "drop",
) -> EvidenceResolutionResult:
    """Resolve every `finding`'s own location and evidence against
    `repo_root`. See the module docstring for the drop/flag semantics.
    """
    reports: list[FindingEvidenceReport] = []
    for finding in findings:
        own = resolve_location(repo_root, finding.file_path, finding.line_start, finding.line_end)
        evidence_resolutions = tuple(
            resolve_location(repo_root, item.file_path, item.line_start, item.line_end)
            for item in finding.evidence
        )
        dropped_evidence_count = sum(1 for r in evidence_resolutions if not r.resolved)
        all_resolved = own.resolved and dropped_evidence_count == 0

        resulting_finding: Finding | None = (
            (finding if all_resolved else None) if mode == "drop" else finding
        )

        reports.append(
            FindingEvidenceReport(
                original_finding=finding,
                own_location=own,
                evidence_resolutions=evidence_resolutions,
                dropped_evidence_count=dropped_evidence_count,
                total_evidence_count=len(finding.evidence),
                all_resolved=all_resolved,
                resulting_finding=resulting_finding,
            )
        )

    survived = [r.resulting_finding for r in reports if r.resulting_finding is not None]
    total_count = len(findings)
    dropped_count = total_count - len(survived)
    drop_rate = dropped_count / total_count if total_count else 0.0

    return EvidenceResolutionResult(
        reports=tuple(reports),
        survived=survived,
        dropped_count=dropped_count,
        total_count=total_count,
        drop_rate=drop_rate,
    )


__all__ = [
    "LocationResolution",
    "resolve_location",
    "FindingEvidenceReport",
    "EvidenceResolutionResult",
    "resolve_evidence",
]
