"""Turning scored runs into the tables that go in the report (§10.3, §10.6).

Repeats: each configuration may run every PR several times (temperature as
deployed). Per-PR values are averaged over repeats before PRs are compared,
and the spread across repeats is reported separately.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import numpy as np

from revu_bench.matching import Judge, MatchMode
from revu_bench.metrics import LEVELS, PRScore, Summary, score_record, summarize
from revu_bench.schema import BenchInstance, RunRecord
from revu_bench.stats import (
    cliffs_delta,
    cliffs_magnitude,
    holm,
    paired_bootstrap,
    wilcoxon_signed_rank,
)


def score_all(
    instances: Sequence[BenchInstance],
    records: Sequence[RunRecord],
    *,
    mode: MatchMode = "location",
    tolerance: int = 3,
    judge: Judge | None = None,
) -> dict[str, list[PRScore]]:
    by_id = {i.id: i for i in instances}
    scores: dict[str, list[PRScore]] = defaultdict(list)
    for record in records:
        instance = by_id.get(record.instance_id)
        if instance is None:
            continue  # a record for an instance outside this split
        scores[record.config].append(
            score_record(instance, record, mode=mode, tolerance=tolerance, judge=judge)
        )
    return dict(scores)


def per_pr_f1(scores: Sequence[PRScore]) -> dict[str, float]:
    """Mean per-PR F1 over repeats, keyed by instance id."""
    values: dict[str, list[float]] = defaultdict(list)
    for s in scores:
        values[s.instance_id].append(s.f1 if s.f1 is not None else 0.0)
    return {k: float(np.mean(v)) for k, v in values.items()}


def repeat_spread(scores: Sequence[PRScore]) -> tuple[float, float] | None:
    """Mean ± standard deviation of micro F1 across repeats."""
    by_repeat: dict[int, list[PRScore]] = defaultdict(list)
    for s in scores:
        by_repeat[s.repeat].append(s)
    if len(by_repeat) < 2:
        return None
    f1s = [summarize("", group).f1 or 0.0 for group in by_repeat.values()]
    return float(np.mean(f1s)), float(np.std(f1s, ddof=1))


def _fmt(value: float | None, digits: int = 3) -> str:
    return "–" if value is None else f"{value:.{digits}f}"


def summary_table(summaries: Sequence[Summary]) -> str:
    header = (
        "| System | PRs | Errors | Precision | Recall | F1 | Macro F1 | "
        + " | ".join(f"Recall {lvl}" for lvl in LEVELS)
        + " | Noise | Findings/PR | ECE | $ / PR | p50 s | p95 s |"
    )
    lines = [header, "|" + "---|" * (header.count("|") - 1)]
    for s in summaries:
        lines.append(
            f"| {s.config} | {s.n_prs} | {s.n_errors} | {_fmt(s.precision)} | {_fmt(s.recall)} "
            f"| {_fmt(s.f1)} | {_fmt(s.macro_f1)} | "
            + " | ".join(_fmt(s.recall_by_level[lvl]) for lvl in LEVELS)
            + f" | {_fmt(s.noise_rate)} | {s.findings_per_pr_mean:.1f} | {_fmt(s.ece)} "
            f"| {s.cost_usd_per_pr:.4f} | {_fmt(s.latency_p50_s, 1)} | {_fmt(s.latency_p95_s, 1)} |"
        )
    return "\n".join(lines)


def comparison_table(scores: dict[str, list[PRScore]], baseline: str, *, seed: int = 0) -> str:
    """Every other system against `baseline` on per-PR F1, PRs both ran:
    paired bootstrap CI of the difference, Wilcoxon p (Holm-adjusted across
    the comparisons in this table) and Cliff's delta."""
    if baseline not in scores:
        raise ValueError(f"no results for baseline {baseline!r}")
    base = per_pr_f1(scores[baseline])
    rows: list[tuple[str, int, float, float, float, float, float]] = []
    for config, config_scores in sorted(scores.items()):
        if config == baseline:
            continue
        other = per_pr_f1(config_scores)
        shared = sorted(set(base) & set(other))
        if len(shared) < 2:
            continue
        a = [other[i] for i in shared]
        b = [base[i] for i in shared]
        ci = paired_bootstrap(a, b, seed=seed)
        test = wilcoxon_signed_rank(a, b)
        rows.append(
            (config, len(shared), ci.estimate, ci.low, ci.high, test.p_value, cliffs_delta(a, b))
        )
    adjusted = holm([r[5] for r in rows])
    lines = [
        f"| System vs {baseline} | Paired PRs | Δ F1 | 95% CI | Wilcoxon p | Holm p | Cliff's δ |",
        "|---|---|---|---|---|---|---|",
    ]
    for (config, n, est, low, high, p, delta), p_holm in zip(rows, adjusted, strict=True):
        lines.append(
            f"| {config} | {n} | {est:+.3f} | [{low:+.3f}, {high:+.3f}] | {p:.4f} | {p_holm:.4f} "
            f"| {delta:+.3f} ({cliffs_magnitude(delta)}) |"
        )
    return "\n".join(lines)


def write_report(
    path: Path,
    instances: Sequence[BenchInstance],
    records: Sequence[RunRecord],
    *,
    baseline: str | None,
    title: str,
    data_fingerprint: str,
    mode: MatchMode = "location",
    tolerance: int = 3,
    judge: Judge | None = None,
) -> dict[str, Summary]:
    scores = score_all(instances, records, mode=mode, tolerance=tolerance, judge=judge)
    summaries = {config: summarize(config, s) for config, s in sorted(scores.items())}
    parts = [
        f"# {title}",
        "",
        f"- Instances: {len(instances)} (data fingerprint `{data_fingerprint}`)",
        f"- Matching: `{mode}`, ±{tolerance} lines",
        "- Unit of analysis: the pull request; failed runs count as finding nothing.",
        "",
        "## Results",
        "",
        summary_table(list(summaries.values())),
    ]
    spreads = {c: repeat_spread(s) for c, s in scores.items()}
    if any(spreads.values()):
        parts += ["", "## Run-to-run variation (micro F1 across repeats)", ""]
        parts += [
            f"- {c}: {m:.3f} ± {sd:.3f}" for c, v in sorted(spreads.items()) if v for m, sd in [v]
        ]
    if baseline and len(scores) > 1:
        parts += ["", f"## Compared with {baseline}", "", comparison_table(scores, baseline)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")

    csv_path = path.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        fields = (
            [
                k
                for k in asdict(next(iter(summaries.values())))
                if k not in ("recall_by_level", "extra")
            ]
            if summaries
            else []
        )
        writer.writerow(fields + [f"recall_{lvl}" for lvl in LEVELS])
        for s in summaries.values():
            row = asdict(s)
            writer.writerow([row[k] for k in fields] + [s.recall_by_level[lvl] for lvl in LEVELS])
    return summaries
