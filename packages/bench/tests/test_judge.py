"""The judge's pair selection, verdict cache, scoring and agreement, with the
model replaced."""

import csv
from pathlib import Path

import pytest
from revu_bench.judge import (
    Pair,
    VerdictCache,
    cached_judge,
    candidate_pairs,
    cohens_kappa,
    export_for_labelling,
    judge_pairs,
    read_labels,
)
from revu_bench.matching import match
from revu_bench.schema import BenchInstance, GroundTruthComment, PredictedFinding, RunRecord

GT = [
    GroundTruthComment(
        path="a.py", line_start=10, line_end=12, text="Division by zero when xs is empty."
    ),
    GroundTruthComment(path="a.py", line_start=40, line_end=40, text="Rename this variable."),
    GroundTruthComment(path="b.py", line_start=1, line_end=1, text="Unused import."),
]
INSTANCE = BenchInstance(id="pr1", repo="o/r", diff="d", ground_truth=GT)
NEAR = PredictedFinding(path="a.py", line_start=11, line_end=11, message="len(xs) can be 0 here.")
FAR = PredictedFinding(
    path="a.py", line_start=90, line_end=90, message="Empty list crashes average()."
)
OTHER_FILE = PredictedFinding(path="c.py", line_start=1, line_end=1, message="Something.")
RECORD = RunRecord(instance_id="pr1", config="sys", findings=[NEAR, FAR, OTHER_FILE, NEAR])


def test_strict_asks_only_about_nearby_pairs_and_lenient_about_the_whole_file() -> None:
    strict = candidate_pairs([INSTANCE], [RECORD], mode="strict", tolerance=3)
    lenient = candidate_pairs([INSTANCE], [RECORD], mode="lenient")

    assert [(p.finding, p.reference.line_start) for p in strict] == [
        (NEAR, 10)
    ]  # NEAR twice: asked once
    assert {(p.finding.line_start, p.reference.line_start) for p in lenient} == {
        (11, 10),
        (11, 40),
        (90, 10),
        (90, 40),
    }


async def test_verdicts_are_asked_once_cached_and_reused(tmp_path: Path) -> None:
    pairs = candidate_pairs([INSTANCE], [RECORD], mode="lenient")
    asked: list[Pair] = []

    async def ask(pair: Pair) -> tuple[bool, str, float]:
        asked.append(pair)
        return "zero" in pair.reference.text, "same defect", 0.001

    cache = VerdictCache(tmp_path / "verdicts.jsonl")
    spent = await judge_pairs(pairs, ask, cache, judge_model="judge-a")
    again = await judge_pairs(
        pairs, ask, VerdictCache(tmp_path / "verdicts.jsonl"), judge_model="judge-a"
    )
    other_model = await judge_pairs(pairs, ask, cache, judge_model="judge-b")

    assert len(asked) == 8  # 4 for judge-a, none on the rerun, 4 again for a different judge
    assert spent == pytest.approx(0.004) and again == 0.0 and other_model == pytest.approx(0.004)


async def test_lenient_scoring_uses_the_cached_verdicts_and_refuses_missing_ones(
    tmp_path: Path,
) -> None:
    pairs = candidate_pairs([INSTANCE], [RECORD], mode="lenient")

    async def ask(pair: Pair) -> tuple[bool, str, float]:
        return "zero" in pair.reference.text, "", 0.0

    cache = VerdictCache(tmp_path / "v.jsonl")
    await judge_pairs(pairs, ask, cache, judge_model="j")
    judge = cached_judge(cache, "j")

    result = match([NEAR, FAR], GT, mode="lenient", judge=judge)
    assert result.tp == 1  # both describe the empty-list bug; one-to-one allows one match
    with pytest.raises(KeyError):
        cached_judge(cache, "never-run")(NEAR, GT[0])


async def test_a_failed_call_is_left_for_the_next_run(tmp_path: Path) -> None:
    pairs = candidate_pairs([INSTANCE], [RECORD], mode="strict")

    async def broken(pair: Pair) -> tuple[bool, str, float]:
        raise TimeoutError

    cache = VerdictCache(tmp_path / "v.jsonl")
    await judge_pairs(pairs, broken, cache, judge_model="j")

    assert cache.verdicts == {}


async def test_the_labelling_sample_is_blind_and_balanced_and_scores_kappa(tmp_path: Path) -> None:
    pairs = candidate_pairs([INSTANCE], [RECORD], mode="lenient")

    async def ask(pair: Pair) -> tuple[bool, str, float]:
        return "zero" in pair.reference.text, "", 0.0

    cache = VerdictCache(tmp_path / "v.jsonl")
    await judge_pairs(pairs, ask, cache, judge_model="j")
    out = tmp_path / "label.csv"

    assert export_for_labelling(pairs, cache, "j", out, n=4, seed=1) == 4
    with out.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {r["same_issue"] for r in rows} == {""}  # the verdict isn't shown
    # A person agrees on every pair but one.
    for i, row in enumerate(rows):
        judged = cache.verdicts[row["key"]][0]
        row["same_issue"] = "yes" if (judged if i else not judged) else "no"
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    labels = read_labels(out)
    agreement = cohens_kappa([(cache.verdicts[k][0], v) for k, v in labels.items()])
    assert agreement.n == 4 and agreement.observed == 0.75
    assert agreement.kappa == pytest.approx(0.5)


def test_kappa_is_one_for_perfect_agreement_and_undefined_without_variation() -> None:
    assert cohens_kappa([(True, True), (False, False)]).kappa == 1.0
    assert cohens_kappa([(True, True), (True, True)]).kappa is None
    assert cohens_kappa([]).n == 0
