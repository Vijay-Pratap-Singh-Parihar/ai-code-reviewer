"""Recompute a merged finding's confidence from three signals: the source
agent(s)' own reported confidence, how many of its evidence items survived
resolution, and how many independent findings agreed on it.

**Weights are chosen, not tuned against any benchmark - same honesty as
`revu.context.rank.RankWeights`** (no ground-truth dataset exists in this
track to tune against; see `IMPLEMENTATION_PLAN.md` Stage 6/8's scope
adaptations). The reasoning behind each weight:

- `own_confidence` (default 0.5) - the dominant signal, since it's the only
  one grounded in the model's own stated certainty about the specific
  claim; everything else here is a *correction* on top of that, not a
  replacement for it.
- `agreement` (default 0.3) - weighted second-highest deliberately: the
  architecture doc's own literature mapping (`Product_Architecture_
  FullStack.md` §8) cites multi-review aggregation lifting F1 by up to
  43.67%, i.e. independent agreement is empirically one of the strongest
  precision levers in this literature, stronger than any single heuristic
  about evidence count.
- `evidence_survival` (default 0.2) - weighted lowest: it's a real,
  hallucination-sensitive signal (see `verify/evidence.py`) but a finding
  can legitimately cite zero evidence and still be correct (e.g. an
  agent's own direct observation of the diff needs no supporting evidence
  item), so it shouldn't dominate.

Weights sum to 1.0 so a finding with maximal own-confidence, full evidence
survival, and full agreement scores exactly 1.0; the result is still
clamped to `[0, 1]` defensively.
"""

from __future__ import annotations

from dataclasses import dataclass

from revu.models import Finding
from revu.verify.dedup import MergedFinding

# Neutral ratio used when a finding cites no evidence at all - neither
# rewarded nor penalized, since an empty evidence list isn't itself a sign
# of fabrication (see the module docstring's `evidence_survival` note).
_NO_EVIDENCE_NEUTRAL_RATIO = 0.5


@dataclass(frozen=True)
class ConfidenceWeights:
    own_confidence: float = 0.5
    evidence_survival: float = 0.2
    agreement: float = 0.3


DEFAULT_WEIGHTS = ConfidenceWeights()


def agreement_score(source_count: int) -> float:
    """`0.0` for a single, uncorroborated finding, approaching `1.0` as more
    independent findings agree (`1 - 1/source_count`): a second agreeing
    finding is worth a lot (0.0 -> 0.5), a tenth is worth comparatively
    little on the margin (0.9 -> 0.9), which matches the intuition that two
    independent sources catching the same issue is already strong signal
    and a diminishing-returns curve is more honest than a linear one.
    """
    if source_count <= 1:
        return 0.0
    return 1.0 - 1.0 / source_count


def evidence_survival_ratio(evidence_survived: int, evidence_total: int) -> float:
    if evidence_total <= 0:
        return _NO_EVIDENCE_NEUTRAL_RATIO
    return evidence_survived / evidence_total


def compute_confidence(
    *,
    own_confidence: float,
    source_count: int,
    evidence_survived: int,
    evidence_total: int,
    weights: ConfidenceWeights = DEFAULT_WEIGHTS,
) -> float:
    """Pure scoring function - no `Finding`/`MergedFinding` dependency, so
    it's directly unit-testable with hand-picked numbers.
    """
    combined = (
        weights.own_confidence * own_confidence
        + weights.evidence_survival * evidence_survival_ratio(evidence_survived, evidence_total)
        + weights.agreement * agreement_score(source_count)
    )
    return max(0.0, min(1.0, combined))


def score_finding_confidence(
    merged: MergedFinding,
    *,
    evidence_survived: int,
    evidence_total: int,
    weights: ConfidenceWeights = DEFAULT_WEIGHTS,
) -> Finding:
    """Return `merged.finding` with its `confidence` field replaced by the
    recomputed score. `own_confidence` is the mean of the source findings'
    own reported confidences (not the max) - averaging is more robust to one
    overconfident lone agent dominating a merged cluster; the separate
    `agreement` term already rewards multiple sources agreeing, so it isn't
    double-counted by also taking the max here.
    """
    own_confidence = (
        sum(merged.source_confidences) / len(merged.source_confidences)
        if merged.source_confidences
        else merged.finding.confidence
    )
    new_confidence = compute_confidence(
        own_confidence=own_confidence,
        source_count=merged.source_count,
        evidence_survived=evidence_survived,
        evidence_total=evidence_total,
        weights=weights,
    )
    return merged.finding.model_copy(update={"confidence": new_confidence})


__all__ = [
    "ConfidenceWeights",
    "DEFAULT_WEIGHTS",
    "agreement_score",
    "evidence_survival_ratio",
    "compute_confidence",
    "score_finding_confidence",
]
