from collections.abc import Callable

from revu.models import EvidenceItem, Finding, FindingCategory, Severity
from revu.verify.dedup import dedup_findings

FindingFactory = Callable[..., Finding]
EvidenceFactory = Callable[..., EvidenceItem]


def test_overlapping_same_file_same_category_merges(finding_factory: FindingFactory) -> None:
    a = finding_factory(
        line_start=10, line_end=15, message="looks like an off-by-one", agent_name="diff_only",
        confidence=0.6,
    )
    b = finding_factory(
        line_start=13, line_end=18, message="index may go out of bounds", agent_name="cross_file",
        confidence=0.9,
    )

    merged = dedup_findings([a, b])

    assert len(merged) == 1
    result = merged[0]
    assert result.source_count == 2
    assert set(result.source_agent_names) == {"diff_only", "cross_file"}
    assert result.source_confidences == (0.6, 0.9)
    assert result.finding.line_start == 10
    assert result.finding.line_end == 18
    assert "off-by-one" in result.finding.message
    assert "out of bounds" in result.finding.message
    assert result.finding.agent_name == "cross_file+diff_only"  # sorted, joined


def test_distinct_different_files_not_merged(finding_factory: FindingFactory) -> None:
    a = finding_factory(file_path="src/a.py", line_start=10, line_end=12)
    b = finding_factory(file_path="src/b.py", line_start=10, line_end=12)

    merged = dedup_findings([a, b])

    assert len(merged) == 2
    assert all(m.source_count == 1 for m in merged)


def test_distinct_same_file_different_category_not_merged(finding_factory: FindingFactory) -> None:
    a = finding_factory(line_start=10, line_end=12, category=FindingCategory.CORRECTNESS)
    b = finding_factory(line_start=10, line_end=12, category=FindingCategory.SECURITY)

    merged = dedup_findings([a, b])

    assert len(merged) == 2
    assert all(m.source_count == 1 for m in merged)


def test_distinct_same_file_far_apart_lines_not_merged(finding_factory: FindingFactory) -> None:
    a = finding_factory(line_start=10, line_end=12)
    b = finding_factory(line_start=200, line_end=205)

    merged = dedup_findings([a, b], line_gap_tolerance=2)

    assert len(merged) == 2
    assert all(m.source_count == 1 for m in merged)


def test_close_within_tolerance_merges(finding_factory: FindingFactory) -> None:
    a = finding_factory(line_start=10, line_end=12)
    b = finding_factory(line_start=14, line_end=16)  # gap of 1 line (13)

    merged = dedup_findings([a, b], line_gap_tolerance=2)

    assert len(merged) == 1
    assert merged[0].source_count == 2


def test_gap_beyond_tolerance_does_not_merge(finding_factory: FindingFactory) -> None:
    a = finding_factory(line_start=10, line_end=12)
    b = finding_factory(line_start=16, line_end=18)  # gap of 3 lines (13,14,15)

    merged = dedup_findings([a, b], line_gap_tolerance=2)

    assert len(merged) == 2


def test_single_agent_overlapping_findings_do_not_duplicate_agent_name(
    finding_factory: FindingFactory,
) -> None:
    a = finding_factory(line_start=10, line_end=12, agent_name="cross_file")
    b = finding_factory(line_start=11, line_end=13, agent_name="cross_file")

    merged = dedup_findings([a, b])

    assert len(merged) == 1
    assert merged[0].finding.agent_name == "cross_file"


def test_transitive_chain_of_three_merges_into_one(finding_factory: FindingFactory) -> None:
    a = finding_factory(line_start=10, line_end=14)
    b = finding_factory(line_start=13, line_end=17)
    c = finding_factory(line_start=16, line_end=20)

    merged = dedup_findings([a, b, c])

    assert len(merged) == 1
    assert merged[0].source_count == 3
    assert merged[0].finding.line_start == 10
    assert merged[0].finding.line_end == 20


def test_severity_of_merged_finding_is_the_worst_of_the_sources(
    finding_factory: FindingFactory,
) -> None:
    a = finding_factory(line_start=10, line_end=12, severity=Severity.LOW)
    b = finding_factory(line_start=11, line_end=13, severity=Severity.CRITICAL)

    merged = dedup_findings([a, b])

    assert merged[0].finding.severity == Severity.CRITICAL


def test_evidence_lists_are_combined_and_deduplicated(
    finding_factory: FindingFactory, evidence_factory: EvidenceFactory
) -> None:
    shared = evidence_factory(file_path="src/shared.py", line_start=1, line_end=2)
    a = finding_factory(line_start=10, line_end=12, evidence=[shared])
    b = finding_factory(
        line_start=11, line_end=13, evidence=[shared, evidence_factory(file_path="src/other.py")]
    )

    merged = dedup_findings([a, b])

    assert len(merged[0].finding.evidence) == 2  # `shared` de-duplicated, not counted twice


def test_identical_messages_are_not_duplicated_in_the_merge(
    finding_factory: FindingFactory,
) -> None:
    a = finding_factory(line_start=10, line_end=12, message="same wording")
    b = finding_factory(line_start=11, line_end=13, message="same wording")

    merged = dedup_findings([a, b])

    assert merged[0].finding.message == "same wording"


def test_output_order_follows_first_seen_input_order(finding_factory: FindingFactory) -> None:
    a = finding_factory(file_path="src/z.py", line_start=1, line_end=2)
    b = finding_factory(file_path="src/a.py", line_start=1, line_end=2)

    merged = dedup_findings([a, b])

    assert [m.finding.file_path for m in merged] == ["src/z.py", "src/a.py"]
