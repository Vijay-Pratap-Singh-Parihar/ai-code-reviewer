"""Verifier/aggregator: dedup, evidence resolution, confidence scoring,
threshold + comment cap.

**Scope adaptation from the original roadmap (Phase 6), stated up front the
same way Stage 6 and Stage 7 documented their own adaptations:** the
original spec calls for `scripts/sweep_threshold.py`, producing a
precision/recall curve over threshold tau measured against benchmark ground
truth. This does not apply here - `IMPLEMENTATION_PLAN.md`'s "Explicitly not
covered" section documents that this app-first track dropped the entire
benchmarking harness; there is no ground-truth dataset to sweep tau against.
The gate for this stage instead is a well-tested verifier library validated
by hand-constructed cases that prove each mechanism actually works, in
particular a hand-verified case proving the evidence-resolution step
(`verify/evidence.py`) genuinely catches a fabricated citation against a
real indexed repository - see `packages/engine/tests/verify/test_evidence_integration.py`.

`verify_findings` is the single entry point, mirroring
`revu.agents.diff_only.review_diff`/`revu.agents.cross_file.review_cross_file`/
`revu.context.bundle.build_context_bundle`'s "one function, plain keyword
configuration" shape. Pipeline order: dedup -> evidence resolution ->
confidence scoring -> threshold + cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from revu.models import Finding
from revu.verify.confidence import ConfidenceWeights, score_finding_confidence
from revu.verify.dedup import DEFAULT_LINE_GAP_TOLERANCE, dedup_findings
from revu.verify.evidence import resolve_evidence
from revu.verify.rank import DEFAULT_MAX_FINDINGS, DEFAULT_THRESHOLD, rank_findings


@dataclass(frozen=True)
class VerificationConfig:
    line_gap_tolerance: int = DEFAULT_LINE_GAP_TOLERANCE
    evidence_mode: Literal["drop", "flag"] = "drop"
    confidence_weights: ConfidenceWeights = field(default_factory=ConfidenceWeights)
    threshold: float = DEFAULT_THRESHOLD
    max_findings: int = DEFAULT_MAX_FINDINGS


@dataclass(frozen=True)
class VerificationReport:
    """Every number a caller (or a future UI) might want to show for "what
    did the verifier actually do to this run's findings" - not just log
    lines, real inspectable data per the task's explicit requirement.
    """

    findings_in: int
    findings_after_dedup: int
    merged_count: int
    evidence_drop_rate: float
    evidence_dropped_count: int
    dropped_below_threshold: int
    cut_by_cap: int
    findings_out: int


@dataclass(frozen=True)
class VerificationResult:
    findings: list[Finding]
    report: VerificationReport


def verify_findings(
    findings: list[Finding],
    *,
    repo_root: Path,
    config: VerificationConfig | None = None,
) -> VerificationResult:
    """Run the full verifier/aggregator pipeline over `findings` (from one or
    more source agents - `dedup_findings` doesn't assume a single agent's
    output, even though today's only real callers produce one agent's
    findings at a time).

    `repo_root` must be a real working tree matching the file paths the
    findings/evidence cite - evidence resolution reads from it directly.
    """
    cfg = config or VerificationConfig()
    findings_in = len(findings)

    merged = dedup_findings(findings, line_gap_tolerance=cfg.line_gap_tolerance)
    merged_count = sum(1 for m in merged if m.source_count > 1)

    evidence_result = resolve_evidence(
        [m.finding for m in merged], repo_root=repo_root, mode=cfg.evidence_mode
    )

    scored: list[Finding] = []
    for merged_finding, evidence_report in zip(merged, evidence_result.reports, strict=True):
        if evidence_report.resulting_finding is None:
            continue
        evidence_survived = (
            evidence_report.total_evidence_count - evidence_report.dropped_evidence_count
        )
        scored.append(
            score_finding_confidence(
                merged_finding,
                evidence_survived=evidence_survived,
                evidence_total=evidence_report.total_evidence_count,
                weights=cfg.confidence_weights,
            )
        )

    rank_result = rank_findings(scored, threshold=cfg.threshold, max_findings=cfg.max_findings)

    report = VerificationReport(
        findings_in=findings_in,
        findings_after_dedup=len(merged),
        merged_count=merged_count,
        evidence_drop_rate=evidence_result.drop_rate,
        evidence_dropped_count=evidence_result.dropped_count,
        dropped_below_threshold=rank_result.dropped_below_threshold,
        cut_by_cap=rank_result.cut_by_cap,
        findings_out=len(rank_result.findings),
    )
    return VerificationResult(findings=rank_result.findings, report=report)


__all__ = [
    "VerificationConfig",
    "VerificationReport",
    "VerificationResult",
    "verify_findings",
]
