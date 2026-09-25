"""Tests for `verify.verify_findings`, the single entry point wiring
dedup -> evidence resolution -> confidence scoring -> threshold/cap.
"""

from collections.abc import Callable
from pathlib import Path

from revu.models import EvidenceItem, Finding
from revu.verify import VerificationConfig, verify_findings

FindingFactory = Callable[..., Finding]
EvidenceFactory = Callable[..., EvidenceItem]


def _write_file(tmp_path: Path, relative_path: str, lines: int = 30) -> None:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"line {i}" for i in range(1, lines + 1)))


def test_full_pipeline_merges_two_agreeing_findings_and_boosts_confidence(
    tmp_path: Path, finding_factory: FindingFactory
) -> None:
    _write_file(tmp_path, "app.py")
    solo = finding_factory(
        file_path="app.py", line_start=10, line_end=12, confidence=0.9, agent_name="diff_only"
    )

    solo_result = verify_findings([solo], repo_root=tmp_path)
    assert len(solo_result.findings) == 1
    solo_confidence = solo_result.findings[0].confidence

    agreeing_a = finding_factory(
        file_path="app.py", line_start=10, line_end=12, confidence=0.9, agent_name="diff_only"
    )
    agreeing_b = finding_factory(
        file_path="app.py", line_start=11, line_end=13, confidence=0.9, agent_name="cross_file"
    )
    agreed_result = verify_findings([agreeing_a, agreeing_b], repo_root=tmp_path)

    assert len(agreed_result.findings) == 1
    assert agreed_result.findings[0].confidence >= solo_confidence
    assert agreed_result.report.findings_in == 2
    assert agreed_result.report.findings_after_dedup == 1
    assert agreed_result.report.merged_count == 1


def test_fabricated_evidence_is_dropped_and_reflected_in_the_report(
    tmp_path: Path, finding_factory: FindingFactory, evidence_factory: EvidenceFactory
) -> None:
    _write_file(tmp_path, "app.py")
    real = finding_factory(file_path="app.py", line_start=1, line_end=3, confidence=0.9)
    fabricated = finding_factory(
        file_path="app.py",
        line_start=20,
        line_end=25,
        confidence=0.9,
        evidence=[evidence_factory(file_path="app.py", line_start=1, line_end=9999)],
    )

    result = verify_findings([real, fabricated], repo_root=tmp_path)

    assert len(result.findings) == 1
    assert result.findings[0].line_start == 1
    assert result.report.findings_in == 2
    assert result.report.evidence_dropped_count == 1
    assert result.report.evidence_drop_rate == 0.5


def test_threshold_and_cap_are_configurable_and_applied(
    tmp_path: Path, finding_factory: FindingFactory
) -> None:
    _write_file(tmp_path, "app.py", lines=1000)
    findings = [
        finding_factory(
            file_path="app.py", line_start=10 * i, line_end=10 * i + 1, confidence=0.9
        )
        for i in range(1, 6)
    ]
    # one clearly-distinct low-confidence finding, far from the others
    findings.append(
        finding_factory(file_path="app.py", line_start=900, line_end=901, confidence=0.1)
    )

    result = verify_findings(
        findings, repo_root=tmp_path, config=VerificationConfig(threshold=0.5, max_findings=2)
    )

    assert result.report.dropped_below_threshold == 1
    assert result.report.cut_by_cap == 3
    assert len(result.findings) == 2


def test_report_counts_are_internally_consistent(
    tmp_path: Path, finding_factory: FindingFactory
) -> None:
    _write_file(tmp_path, "app.py", lines=50)
    findings = [
        finding_factory(file_path="app.py", line_start=10 * i, line_end=10 * i + 1, confidence=0.9)
        for i in range(1, 4)
    ]

    result = verify_findings(findings, repo_root=tmp_path)

    assert result.report.findings_in == 3
    assert result.report.findings_out == len(result.findings)
