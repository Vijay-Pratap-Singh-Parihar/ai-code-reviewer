from collections.abc import Callable
from pathlib import Path

from revu.models import EvidenceItem, Finding
from revu.verify.evidence import resolve_evidence, resolve_location

FindingFactory = Callable[..., Finding]
EvidenceFactory = Callable[..., EvidenceItem]


def _write_small_file(tmp_path: Path, relative_path: str, lines: int = 5) -> None:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"line {i}" for i in range(1, lines + 1)))


def test_resolve_location_succeeds_for_a_real_file_and_valid_range(tmp_path: Path) -> None:
    _write_small_file(tmp_path, "app.py", lines=20)

    result = resolve_location(tmp_path, "app.py", 3, 10)

    assert result.resolved is True
    assert result.reason is None


def test_resolve_location_fails_for_missing_file(tmp_path: Path) -> None:
    result = resolve_location(tmp_path, "does_not_exist.py", 1, 1)

    assert result.resolved is False
    assert result.reason is not None


def test_resolve_location_fails_for_line_beyond_file_length(tmp_path: Path) -> None:
    _write_small_file(tmp_path, "app.py", lines=5)

    result = resolve_location(tmp_path, "app.py", 1, 500)

    assert result.resolved is False
    assert "5" in (result.reason or "")


def test_resolve_location_fails_for_path_escaping_repo_root(tmp_path: Path) -> None:
    (tmp_path / "inner").mkdir()

    result = resolve_location(tmp_path / "inner", "../../outside.py", 1, 1)

    assert result.resolved is False


def test_resolve_location_fails_for_invalid_range(tmp_path: Path) -> None:
    _write_small_file(tmp_path, "app.py", lines=5)

    result = resolve_location(tmp_path, "app.py", 5, 2)  # end before start

    assert result.resolved is False


def test_finding_with_valid_own_location_and_no_evidence_survives(
    tmp_path: Path, finding_factory: FindingFactory
) -> None:
    _write_small_file(tmp_path, "app.py", lines=20)
    finding = finding_factory(file_path="app.py", line_start=1, line_end=5, evidence=[])

    result = resolve_evidence([finding], repo_root=tmp_path)

    assert result.survived == [finding]
    assert result.dropped_count == 0
    assert result.drop_rate == 0.0


def test_finding_with_out_of_range_own_location_is_dropped(
    tmp_path: Path, finding_factory: FindingFactory
) -> None:
    _write_small_file(tmp_path, "app.py", lines=5)
    finding = finding_factory(file_path="app.py", line_start=1, line_end=500)

    result = resolve_evidence([finding], repo_root=tmp_path)

    assert result.survived == []
    assert result.dropped_count == 1
    assert result.drop_rate == 1.0


def test_finding_with_one_fabricated_evidence_item_is_dropped_entirely(
    tmp_path: Path, finding_factory: FindingFactory, evidence_factory: EvidenceFactory
) -> None:
    _write_small_file(tmp_path, "app.py", lines=20)
    good_evidence = evidence_factory(file_path="app.py", line_start=1, line_end=2)
    fabricated_evidence = evidence_factory(file_path="app.py", line_start=1, line_end=999)
    finding = finding_factory(
        file_path="app.py", line_start=1, line_end=5, evidence=[good_evidence, fabricated_evidence]
    )

    result = resolve_evidence([finding], repo_root=tmp_path)

    assert result.survived == []
    assert result.dropped_count == 1


def test_flag_mode_never_drops_but_still_reports_the_outcome(
    tmp_path: Path, finding_factory: FindingFactory, evidence_factory: EvidenceFactory
) -> None:
    _write_small_file(tmp_path, "app.py", lines=20)
    fabricated_evidence = evidence_factory(file_path="does_not_exist.py", line_start=1, line_end=2)
    finding = finding_factory(
        file_path="app.py", line_start=1, line_end=5, evidence=[fabricated_evidence]
    )

    result = resolve_evidence([finding], repo_root=tmp_path, mode="flag")

    assert result.survived == [finding]
    assert result.dropped_count == 0
    assert result.reports[0].all_resolved is False
    assert result.reports[0].dropped_evidence_count == 1


def test_drop_rate_reflects_a_mix_of_good_and_fabricated_findings(
    tmp_path: Path, finding_factory: FindingFactory
) -> None:
    _write_small_file(tmp_path, "app.py", lines=20)
    good = finding_factory(file_path="app.py", line_start=1, line_end=5)
    bad = finding_factory(file_path="app.py", line_start=1, line_end=5000)
    also_bad = finding_factory(file_path="ghost.py", line_start=1, line_end=5)

    result = resolve_evidence([good, bad, also_bad], repo_root=tmp_path)

    assert result.survived == [good]
    assert result.dropped_count == 2
    assert result.total_count == 3
    assert result.drop_rate == 2 / 3
