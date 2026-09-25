from collections.abc import Callable

from revu.models import Finding, Severity
from revu.verify.rank import rank_findings

FindingFactory = Callable[..., Finding]


def test_findings_below_threshold_are_dropped(finding_factory: FindingFactory) -> None:
    keep = finding_factory(confidence=0.9)
    drop = finding_factory(confidence=0.2)

    result = rank_findings([keep, drop], threshold=0.5)

    assert result.findings == [keep]
    assert result.dropped_below_threshold == 1
    assert result.total_in == 2


def test_more_than_cap_remaining_is_cut_down_to_n(finding_factory: FindingFactory) -> None:
    findings = [finding_factory(confidence=0.9 - i * 0.01) for i in range(5)]

    result = rank_findings(findings, threshold=0.0, max_findings=3)

    assert len(result.findings) == 3
    assert result.cut_by_cap == 2


def test_cap_keeps_the_highest_severity_and_confidence_findings(
    finding_factory: FindingFactory,
) -> None:
    critical_low_confidence = finding_factory(
        severity=Severity.CRITICAL, confidence=0.51, message="critical"
    )
    low_severity_high_confidence = [
        finding_factory(severity=Severity.LOW, confidence=0.99 - i * 0.01, message=f"low-{i}")
        for i in range(5)
    ]

    result = rank_findings(
        [critical_low_confidence, *low_severity_high_confidence], threshold=0.5, max_findings=3
    )

    assert critical_low_confidence in result.findings  # never bumped out by the cap
    assert result.findings[0] is critical_low_confidence  # severity beats confidence in sort order
    assert len(result.findings) == 3


def test_sorted_by_severity_then_confidence(finding_factory: FindingFactory) -> None:
    low = finding_factory(severity=Severity.LOW, confidence=0.99, message="low")
    high_lower_confidence = finding_factory(
        severity=Severity.HIGH, confidence=0.6, message="high-a"
    )
    high_higher_confidence = finding_factory(
        severity=Severity.HIGH, confidence=0.8, message="high-b"
    )

    result = rank_findings(
        [low, high_lower_confidence, high_higher_confidence], threshold=0.0, max_findings=10
    )

    assert result.findings == [high_higher_confidence, high_lower_confidence, low]


def test_dropped_below_threshold_and_cut_by_cap_counts_are_independent(
    finding_factory: FindingFactory,
) -> None:
    below_threshold = finding_factory(confidence=0.1)
    above_threshold = [finding_factory(confidence=0.9 - i * 0.01) for i in range(4)]

    result = rank_findings(
        [below_threshold, *above_threshold], threshold=0.5, max_findings=2
    )

    assert result.dropped_below_threshold == 1
    assert result.cut_by_cap == 2
    assert len(result.findings) == 2
