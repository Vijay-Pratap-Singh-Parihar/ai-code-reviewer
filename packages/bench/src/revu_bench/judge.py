"""The LLM judge for `strict` and `lenient` matching (§10.2), and its
validation against human labels.

Judging is done ahead of scoring: every plausible (finding, reference)
pair is collected, each one is sent to the judge model once, and the
verdicts are cached on disk. Scoring then uses a judge that only looks
verdicts up, so a report can be regenerated for free and always gives the
same numbers. The cache key covers the judge model and the prompt version:
changing either asks again instead of mixing verdicts.

Validation: a sample of judged pairs is exported for a person to label
without seeing the judge's verdict; Cohen's κ between the two says whether
the judge's matches can be trusted (κ ≥ 0.6 is "substantial" agreement).
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from revu.providers.llm import ModelEndpoint, complete, extract_json

from revu_bench.matching import DEFAULT_TOLERANCE, Judge, MatchMode, line_gap, normalize_path
from revu_bench.schema import BenchInstance, GroundTruthComment, PredictedFinding, RunRecord

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You judge an automated code reviewer against a human code review.
You get one comment written by the reviewer under test and one reference comment from an
expert review of the same pull request, both on the same file. Decide whether they raise the
SAME underlying issue: the same defect, risk or improvement at essentially the same place,
even if worded differently or one is more detailed. Different issues on the same lines are
NOT the same. A vague comment that would not lead a developer to the reference's issue is NOT
the same.
Answer with a JSON object only: {"same_issue": true or false, "reason": "<one sentence>"}"""


@dataclass(frozen=True)
class Pair:
    finding: PredictedFinding
    reference: GroundTruthComment

    def key(self, judge_model: str) -> str:
        payload = json.dumps(
            [
                judge_model,
                PROMPT_VERSION,
                normalize_path(self.finding.path),
                self.finding.line_start,
                self.finding.line_end,
                self.finding.message,
                normalize_path(self.reference.path),
                self.reference.line_start,
                self.reference.line_end,
                self.reference.text,
            ]
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:24]


def candidate_pairs(
    instances: Sequence[BenchInstance],
    records: Sequence[RunRecord],
    *,
    mode: MatchMode,
    tolerance: int = DEFAULT_TOLERANCE,
) -> list[Pair]:
    """Every pair the matcher would ask the judge about: same file, and for
    `strict` also within `tolerance` lines. Duplicates are asked once."""
    by_id = {i.id: i for i in instances}
    seen: set[tuple[str, ...]] = set()
    pairs: list[Pair] = []
    for record in records:
        instance = by_id.get(record.instance_id)
        if instance is None:
            continue
        for finding in record.findings:
            for reference in instance.ground_truth:
                if normalize_path(finding.path) != normalize_path(reference.path):
                    continue
                if mode == "strict" and line_gap(finding, reference) > tolerance:
                    continue
                pair = Pair(finding, reference)
                ident = (pair.key(""),)
                if ident not in seen:
                    seen.add(ident)
                    pairs.append(pair)
    return pairs


class VerdictCache:
    """Append-only JSONL of `{"key", "same_issue", "reason"}`."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.verdicts: dict[str, tuple[bool, str]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.verdicts[row["key"]] = (bool(row["same_issue"]), row.get("reason", ""))

    def add(self, key: str, same_issue: bool, reason: str) -> None:
        self.verdicts[key] = (same_issue, reason)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            row = {"key": key, "same_issue": same_issue, "reason": reason}
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _describe(pair: Pair) -> str:
    f, r = pair.finding, pair.reference
    return (
        f"File: {normalize_path(f.path)}\n\n"
        f"Reviewer under test (lines {f.line_start}-{f.line_end}):\n{f.message}\n\n"
        f"Reference comment (lines {r.line_start}-{r.line_end}):\n{r.text}"
    )


Ask = Callable[[Pair], Awaitable[tuple[bool, str, float]]]


def llm_ask(endpoint: ModelEndpoint) -> Ask:
    async def ask(pair: Pair) -> tuple[bool, str, float]:
        result = await complete(
            model=endpoint,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _describe(pair)},
            ],
            response_format={"type": "json_object"},
            max_tokens=200,
            temperature=0.0,
        )
        verdict = extract_json(result.content)
        reason = str(verdict.get("reason", ""))[:300]
        return verdict.get("same_issue") is True, reason, result.cost_usd

    return ask


async def judge_pairs(
    pairs: Sequence[Pair],
    ask: Ask,
    cache: VerdictCache,
    *,
    judge_model: str,
    concurrency: int = 4,
    budget_usd: float = 0.0,
    on_verdict: Callable[[int, int], None] | None = None,
) -> float:
    """Ask about every pair not yet in `cache`; returns what it cost. Stops
    before the spend passes `budget_usd` (0 = free models only, enforced by
    the caller). A pair whose call fails is left out of the cache and asked
    again next time."""
    todo = [p for p in pairs if p.key(judge_model) not in cache.verdicts]
    spent = 0.0
    done = 0
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(pair: Pair) -> None:
        nonlocal spent, done
        async with semaphore:
            if budget_usd and spent > budget_usd:
                return
            try:
                same, reason, cost = await ask(pair)
            except Exception:  # a failed call is retried on the next run
                return
            async with lock:
                cache.add(pair.key(judge_model), same, reason)
                spent += cost
                done += 1
                if on_verdict is not None:
                    on_verdict(done, len(todo))

    await asyncio.gather(*(one(p) for p in todo))
    return spent


def cached_judge(cache: VerdictCache, judge_model: str) -> Judge:
    """A judge that only reads verdicts; a pair that was never judged is an
    error, so a report can't silently score with missing verdicts."""

    def judge(finding: PredictedFinding, reference: GroundTruthComment) -> bool:
        key = Pair(finding, reference).key(judge_model)
        if key not in cache.verdicts:
            raise KeyError("a pair has no verdict yet; run the `judge` command first")
        return cache.verdicts[key][0]

    return judge


# --- validation against people -------------------------------------------------


def export_for_labelling(
    pairs: Sequence[Pair], cache: VerdictCache, judge_model: str, path: Path, *, n: int, seed: int
) -> int:
    """A random sample of judged pairs, balanced between the judge's yes and
    no answers so κ isn't dominated by easy negatives, written without the
    verdict for a person to fill in the `same_issue` column (yes/no)."""
    judged = [p for p in pairs if p.key(judge_model) in cache.verdicts]
    yes = [p for p in judged if cache.verdicts[p.key(judge_model)][0]]
    no = [p for p in judged if not cache.verdicts[p.key(judge_model)][0]]
    rng = random.Random(seed)
    sample = rng.sample(yes, min(len(yes), n // 2)) + rng.sample(no, min(len(no), n - n // 2))
    rng.shuffle(sample)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["key", "file", "reviewer_comment", "reference_comment", "same_issue"])
        for p in sample:
            writer.writerow(
                [
                    p.key(judge_model),
                    normalize_path(p.finding.path),
                    f"L{p.finding.line_start}-{p.finding.line_end}: {p.finding.message}",
                    f"L{p.reference.line_start}-{p.reference.line_end}: {p.reference.text}",
                    "",
                ]
            )
    return len(sample)


def read_labels(path: Path) -> dict[str, bool]:
    labels: dict[str, bool] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            value = (row.get("same_issue") or "").strip().lower()
            if value in ("yes", "y", "true", "1"):
                labels[row["key"]] = True
            elif value in ("no", "n", "false", "0"):
                labels[row["key"]] = False
    return labels


@dataclass(frozen=True)
class Agreement:
    n: int
    observed: float
    kappa: float | None


def cohens_kappa(pairs: Sequence[tuple[bool, bool]]) -> Agreement:
    """Agreement between two raters on yes/no answers, corrected for chance."""
    n = len(pairs)
    if n == 0:
        return Agreement(0, 0.0, None)
    observed = sum(a == b for a, b in pairs) / n
    p_a = sum(a for a, _ in pairs) / n
    p_b = sum(b for _, b in pairs) / n
    expected = p_a * p_b + (1 - p_a) * (1 - p_b)
    kappa = None if expected == 1 else (observed - expected) / (1 - expected)
    return Agreement(n, observed, kappa)
