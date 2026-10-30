"""Per-PR scores and their aggregates (§10.3).

Every PR is scored on its own first (`PRScore`); a configuration's summary
(`Summary`) is computed from those, and statistics compare configurations
PR by PR (`revu_bench.stats`). Both micro averages (pooling all comments)
and macro averages (mean over PRs) are reported: micro is what the
benchmark papers report, macro is what the per-PR significance tests use.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from revu_bench.matching import Judge, MatchMode, match
from revu_bench.schema import BenchInstance, ContextLevel, RunRecord

LEVELS: tuple[ContextLevel, ...] = ("diff", "file", "repo")


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


@dataclass
class PRScore:
    instance_id: str
    config: str
    repeat: int
    tp: int
    fp: int
    fn: int
    n_findings: int
    n_ground_truth: int
    found_by_level: dict[str, int]
    total_by_level: dict[str, int]
    # (confidence, matched) for every finding that reported a confidence.
    calibration: list[tuple[float, bool]]
    cost_usd: float
    latency_ms: int
    tokens_in: int
    tokens_out: int
    error: str | None = None

    @property
    def precision(self) -> float | None:
        return _ratio(self.tp, self.n_findings)

    @property
    def recall(self) -> float | None:
        return _ratio(self.tp, self.n_ground_truth)

    @property
    def f1(self) -> float | None:
        # A PR with no ground truth and no findings is a perfect silence;
        # findings on a PR with no ground truth are all false positives.
        if self.n_ground_truth == 0:
            return 1.0 if self.n_findings == 0 else 0.0
        return _f1(self.precision if self.n_findings else 0.0, self.recall)


def score_record(
    instance: BenchInstance,
    record: RunRecord,
    *,
    mode: MatchMode = "location",
    tolerance: int = 3,
    judge: Judge | None = None,
) -> PRScore:
    result = match(
        record.findings, instance.ground_truth, mode=mode, tolerance=tolerance, judge=judge
    )
    found = result.matched_ground_truth
    found_by_level: dict[str, int] = {level: 0 for level in LEVELS}
    total_by_level: dict[str, int] = {level: 0 for level in LEVELS}
    for gi, comment in enumerate(instance.ground_truth):
        if comment.context_level is None:
            continue
        total_by_level[comment.context_level] += 1
        if gi in found:
            found_by_level[comment.context_level] += 1
    matched = result.matched_findings
    calibration = [
        (finding.confidence, fi in matched)
        for fi, finding in enumerate(record.findings)
        if finding.confidence is not None
    ]
    return PRScore(
        instance_id=instance.id,
        config=record.config,
        repeat=record.repeat,
        tp=result.tp,
        fp=result.fp,
        fn=result.fn,
        n_findings=result.n_findings,
        n_ground_truth=result.n_ground_truth,
        found_by_level=found_by_level,
        total_by_level=total_by_level,
        calibration=calibration,
        cost_usd=record.cost_usd,
        latency_ms=record.latency_ms,
        tokens_in=record.tokens_in,
        tokens_out=record.tokens_out,
        error=record.error,
    )


def expected_calibration_error(pairs: Sequence[tuple[float, bool]], bins: int = 10) -> float | None:
    """ECE with equal-width bins: the confidence-weighted gap between how
    sure findings claim to be and how often they are actually right."""
    if not pairs:
        return None
    confidence = np.array([c for c, _ in pairs], dtype=float)
    correct = np.array([m for _, m in pairs], dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    index = np.clip(np.digitize(confidence, edges[1:-1], right=True), 0, bins - 1)
    ece = 0.0
    for b in range(bins):
        mask = index == b
        if mask.any():
            ece += mask.mean() * abs(confidence[mask].mean() - correct[mask].mean())
    return float(ece)


def brier_score(pairs: Sequence[tuple[float, bool]]) -> float | None:
    if not pairs:
        return None
    confidence = np.array([c for c, _ in pairs], dtype=float)
    correct = np.array([m for _, m in pairs], dtype=float)
    return float(np.mean((confidence - correct) ** 2))


def _mean(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return float(np.mean(present)) if present else None


@dataclass
class Summary:
    config: str
    n_prs: int
    n_errors: int
    precision: float | None
    recall: float | None
    f1: float | None
    macro_f1: float | None
    recall_by_level: dict[str, float | None]
    noise_rate: float | None
    findings_per_pr_mean: float
    findings_per_pr_max: int
    ece: float | None
    brier: float | None
    cost_usd_total: float
    cost_usd_per_pr: float
    latency_p50_s: float | None
    latency_p95_s: float | None
    extra: dict[str, float] = field(default_factory=dict)


def summarize(config: str, scores: Sequence[PRScore]) -> Summary:
    """One configuration's results. Failed runs count as PRs where the
    system found nothing (they still had ground truth to find), so a
    flaky configuration can't look better by skipping hard PRs."""
    tp = sum(s.tp for s in scores)
    fp = sum(s.fp for s in scores)
    fn = sum(s.fn for s in scores)
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    pairs = [p for s in scores for p in s.calibration]
    latencies = [s.latency_ms / 1000 for s in scores if s.error is None]
    findings = [s.n_findings for s in scores]
    recall_by_level: dict[str, float | None] = {
        level: _ratio(
            sum(s.found_by_level[level] for s in scores),
            sum(s.total_by_level[level] for s in scores),
        )
        for level in LEVELS
    }
    return Summary(
        config=config,
        n_prs=len(scores),
        n_errors=sum(1 for s in scores if s.error is not None),
        precision=precision,
        recall=recall,
        f1=_f1(precision, recall),
        macro_f1=_mean([s.f1 for s in scores]),
        recall_by_level=recall_by_level,
        noise_rate=_ratio(fp, tp + fp),
        findings_per_pr_mean=float(np.mean(findings)) if findings else 0.0,
        findings_per_pr_max=max(findings, default=0),
        ece=expected_calibration_error(pairs),
        brier=brier_score(pairs),
        cost_usd_total=float(sum(s.cost_usd for s in scores)),
        cost_usd_per_pr=float(np.mean([s.cost_usd for s in scores])) if scores else 0.0,
        latency_p50_s=float(np.percentile(latencies, 50)) if latencies else None,
        latency_p95_s=float(np.percentile(latencies, 95)) if latencies else None,
    )
