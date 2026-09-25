from collections.abc import Callable

from revu.models import Finding
from revu.verify.confidence import (
    ConfidenceWeights,
    agreement_score,
    compute_confidence,
    evidence_survival_ratio,
    score_finding_confidence,
)
from revu.verify.dedup import MergedFinding

FindingFactory = Callable[..., Finding]


def test_agreement_score_is_zero_for_a_single_source() -> None:
    assert agreement_score(1) == 0.0


def test_agreement_score_increases_with_more_sources() -> None:
    assert agreement_score(2) > agreement_score(1)
    assert agreement_score(3) > agreement_score(2)


def test_evidence_survival_ratio_is_neutral_when_no_evidence_cited() -> None:
    assert evidence_survival_ratio(0, 0) == 0.5


def test_evidence_survival_ratio_reflects_partial_survival() -> None:
    assert evidence_survival_ratio(1, 4) == 0.25
    assert evidence_survival_ratio(4, 4) == 1.0


def test_agreement_across_findings_scores_at_least_as_confident_as_alone() -> None:
    solo = compute_confidence(
        own_confidence=0.8, source_count=1, evidence_survived=2, evidence_total=2
    )
    agreed = compute_confidence(
        own_confidence=0.8, source_count=2, evidence_survived=2, evidence_total=2
    )

    assert agreed >= solo
    assert agreed > solo  # strictly higher, since agreement weight is non-zero by default


def test_more_agreeing_sources_scores_at_least_as_high_as_fewer() -> None:
    two_sources = compute_confidence(
        own_confidence=0.8, source_count=2, evidence_survived=2, evidence_total=2
    )
    three_sources = compute_confidence(
        own_confidence=0.8, source_count=3, evidence_survived=2, evidence_total=2
    )

    assert three_sources >= two_sources


def test_lost_evidence_never_scores_higher_than_fully_surviving_evidence() -> None:
    full_evidence = compute_confidence(
        own_confidence=0.8, source_count=1, evidence_survived=3, evidence_total=3
    )
    partial_evidence = compute_confidence(
        own_confidence=0.8, source_count=1, evidence_survived=1, evidence_total=3
    )

    assert partial_evidence < full_evidence


def test_confidence_is_clamped_to_zero_one_range() -> None:
    weights = ConfidenceWeights(own_confidence=2.0, evidence_survival=2.0, agreement=2.0)

    score = compute_confidence(
        own_confidence=1.0, source_count=5, evidence_survived=1, evidence_total=1, weights=weights
    )

    assert score == 1.0


def test_score_finding_confidence_uses_mean_of_source_confidences(
    finding_factory: FindingFactory,
) -> None:
    base = finding_factory(confidence=0.5)
    merged = MergedFinding(
        finding=base, source_count=2, source_agent_names=("a", "b"), source_confidences=(0.4, 0.8)
    )

    scored = score_finding_confidence(merged, evidence_survived=0, evidence_total=0)

    # own_confidence input to the formula is mean(0.4, 0.8) = 0.6, not base.confidence (0.5)
    expected = compute_confidence(
        own_confidence=0.6, source_count=2, evidence_survived=0, evidence_total=0
    )
    assert scored.confidence == expected
    assert scored.file_path == base.file_path  # everything else about the finding is unchanged
