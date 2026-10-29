import pytest
from revu_bench.matching import match, normalize_path
from revu_bench.metrics import brier_score, expected_calibration_error, score_record, summarize
from revu_bench.schema import BenchInstance, GroundTruthComment, PredictedFinding, RunRecord


def gt(
    path: str, start: int, end: int | None = None, level: str | None = None
) -> GroundTruthComment:
    return GroundTruthComment(
        path=path, line_start=start, line_end=end or start, context_level=level
    )  # type: ignore[arg-type]


def pf(
    path: str, start: int, end: int | None = None, confidence: float | None = None
) -> PredictedFinding:
    return PredictedFinding(
        path=path, line_start=start, line_end=end or start, confidence=confidence
    )


def test_paths_are_compared_without_diff_prefixes_or_backslashes() -> None:
    assert normalize_path("b/src/app.py") == normalize_path("src\\app.py") == "src/app.py"


def test_lines_within_the_tolerance_match_and_beyond_it_do_not() -> None:
    truth = [gt("a.py", 10, 12)]
    assert match([pf("a.py", 15)], truth, tolerance=3).tp == 1
    assert match([pf("a.py", 16)], truth, tolerance=3).tp == 0
    assert match([pf("b.py", 11)], truth).tp == 0


def test_matching_is_one_to_one_so_repeating_a_finding_does_not_raise_recall() -> None:
    result = match([pf("a.py", 10), pf("a.py", 10), pf("a.py", 11)], [gt("a.py", 10)])
    assert (result.tp, result.fp, result.fn) == (1, 2, 0)


def test_matching_is_maximum_not_greedy() -> None:
    """Finding 0 could take either comment; taking the nearest one greedily
    would strand finding 1. The maximum matching pairs both."""
    findings = [pf("a.py", 10, 14), pf("a.py", 9)]
    truth = [gt("a.py", 9), gt("a.py", 14)]
    assert match(findings, truth, tolerance=0).tp == 2


def test_strict_and_lenient_modes_ask_the_judge() -> None:
    same_issue = lambda f, g: "null" in f.message  # noqa: E731
    findings = [PredictedFinding(path="a.py", line_start=50, line_end=50, message="null deref")]
    truth = [gt("a.py", 10)]
    assert match(findings, truth, mode="strict", judge=same_issue).tp == 0  # lines too far
    assert match(findings, truth, mode="lenient", judge=same_issue).tp == 1
    with pytest.raises(ValueError):
        match(findings, truth, mode="strict")


def test_a_pr_score_counts_recall_by_context_level() -> None:
    instance = BenchInstance(
        id="pr-1",
        repo="r",
        diff="d",
        ground_truth=[
            gt("a.py", 1, level="diff"),
            gt("b.py", 5, level="repo"),
            gt("c.py", 9, level="repo"),
        ],
    )
    record = RunRecord(
        instance_id="pr-1", config="x", findings=[pf("a.py", 1), pf("c.py", 9), pf("z.py", 1)]
    )

    score = score_record(instance, record)

    assert (score.tp, score.fp, score.fn) == (2, 1, 1)
    assert score.found_by_level == {"diff": 1, "file": 0, "repo": 1}
    assert score.total_by_level == {"diff": 1, "file": 0, "repo": 2}
    assert score.precision == pytest.approx(2 / 3) and score.recall == pytest.approx(2 / 3)


def test_silence_on_a_clean_pr_is_perfect_and_noise_on_it_is_not() -> None:
    clean = BenchInstance(id="clean", repo="r", diff="d")
    quiet = score_record(clean, RunRecord(instance_id="clean", config="x"))
    noisy = score_record(
        clean, RunRecord(instance_id="clean", config="x", findings=[pf("a.py", 1)])
    )
    assert quiet.f1 == 1.0 and noisy.f1 == 0.0


def test_summary_micro_averages_and_counts_failed_runs_as_finding_nothing() -> None:
    instance = BenchInstance(id="p", repo="r", diff="d", ground_truth=[gt("a.py", 1, level="diff")])
    ok = score_record(
        instance, RunRecord(instance_id="p", config="x", findings=[pf("a.py", 1)], cost_usd=0.01)
    )
    failed = score_record(
        instance, RunRecord(instance_id="p", config="x", repeat=1, error="timeout")
    )

    summary = summarize("x", [ok, failed])

    assert (summary.n_prs, summary.n_errors) == (2, 1)
    assert summary.precision == 1.0 and summary.recall == 0.5
    assert summary.recall_by_level["diff"] == 0.5
    assert summary.cost_usd_per_pr == pytest.approx(0.005)


def test_calibration_is_zero_when_confidence_matches_accuracy() -> None:
    assert expected_calibration_error([(1.0, True), (0.0, False)]) == pytest.approx(0.0)
    assert expected_calibration_error([(0.9, False)] * 4) == pytest.approx(0.9)
    assert brier_score([(1.0, True), (1.0, False)]) == pytest.approx(0.5)
    assert expected_calibration_error([]) is None
