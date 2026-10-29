"""Paired statistics across configurations (§10.6).

The unit of analysis is the pull request: two configurations are compared
on the *same* PRs, and every test resamples or ranks per-PR values (comments
within one PR are correlated, so treating them as independent would
overstate significance).

Implemented with numpy and the standard library only:

- `paired_bootstrap`: percentile CI of the mean difference, resampling PRs.
- `wilcoxon_signed_rank`: two-sided; exact null distribution when n <= 25
  and there are no tied |differences|, otherwise the normal approximation
  with tie and continuity corrections. Zero differences are dropped
  (Wilcoxon's original convention).
- `holm`: Holm–Bonferroni adjusted p-values for a family of contrasts.
- `cliffs_delta`: effect size in [-1, 1] (|δ| < 0.147 negligible, < 0.33
  small, < 0.474 medium, otherwise large; Romano et al., 2006).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

EXACT_MAX_N = 25


@dataclass(frozen=True)
class BootstrapCI:
    estimate: float
    low: float
    high: float
    n: int


def bootstrap_mean(
    values: Sequence[float], *, resamples: int = 10_000, seed: int = 0, level: float = 0.95
) -> BootstrapCI:
    data = np.asarray(values, dtype=float)
    if data.size == 0:
        raise ValueError("no values to bootstrap")
    rng = np.random.default_rng(seed)
    means = data[rng.integers(0, data.size, size=(resamples, data.size))].mean(axis=1)
    alpha = (1 - level) / 2
    return BootstrapCI(
        estimate=float(data.mean()),
        low=float(np.quantile(means, alpha)),
        high=float(np.quantile(means, 1 - alpha)),
        n=int(data.size),
    )


def paired_bootstrap(
    a: Sequence[float],
    b: Sequence[float],
    *,
    resamples: int = 10_000,
    seed: int = 0,
    level: float = 0.95,
) -> BootstrapCI:
    """CI for mean(a - b) over PRs: both configurations are resampled with
    the same PR indices, which is what makes it a paired comparison."""
    if len(a) != len(b):
        raise ValueError("paired samples must have the same length (same PRs)")
    return bootstrap_mean(
        np.asarray(a, float) - np.asarray(b, float), resamples=resamples, seed=seed, level=level
    )


@dataclass(frozen=True)
class WilcoxonResult:
    statistic: float  # W+, the sum of ranks of positive differences
    p_value: float
    n: int  # pairs left after dropping zero differences
    method: str  # "exact" or "normal"


def _ranks(values: np.ndarray) -> np.ndarray:
    """Average ranks (1-based), ties sharing the mean of their positions."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    sorted_values = values[order]
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and sorted_values[j + 1] == sorted_values[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j + 2) / 2
        i = j + 1
    return ranks


def _exact_two_sided(w_plus: float, n: int) -> float:
    # Number of subsets of {1..n} with each rank sum: the null distribution
    # of W+ when every sign is equally likely.
    max_sum = n * (n + 1) // 2
    counts = [0] * (max_sum + 1)
    counts[0] = 1
    for rank in range(1, n + 1):
        for total in range(max_sum, rank - 1, -1):
            counts[total] += counts[total - rank]
    total_ways = 2**n
    w = int(round(w_plus))
    lower = sum(counts[: w + 1]) / total_ways
    upper = sum(counts[w:]) / total_ways
    return float(min(1.0, 2 * min(lower, upper)))


def wilcoxon_signed_rank(a: Sequence[float], b: Sequence[float]) -> WilcoxonResult:
    diff = np.asarray(a, float) - np.asarray(b, float)
    if len(a) != len(b):
        raise ValueError("paired samples must have the same length (same PRs)")
    diff = diff[diff != 0]
    n = int(diff.size)
    if n == 0:
        return WilcoxonResult(statistic=0.0, p_value=1.0, n=0, method="exact")
    magnitude = np.abs(diff)
    ranks = _ranks(magnitude)
    w_plus = float(ranks[diff > 0].sum())
    has_ties = len(np.unique(magnitude)) < n
    if n <= EXACT_MAX_N and not has_ties:
        return WilcoxonResult(
            statistic=w_plus, p_value=_exact_two_sided(w_plus, n), n=n, method="exact"
        )

    mean = n * (n + 1) / 4
    _, tie_counts = np.unique(magnitude, return_counts=True)
    variance = n * (n + 1) * (2 * n + 1) / 24 - float(np.sum(tie_counts**3 - tie_counts)) / 48
    if variance <= 0:
        return WilcoxonResult(statistic=w_plus, p_value=1.0, n=n, method="normal")
    z = (abs(w_plus - mean) - 0.5) / math.sqrt(variance)
    p = math.erfc(max(z, 0.0) / math.sqrt(2))
    return WilcoxonResult(statistic=w_plus, p_value=min(1.0, p), n=n, method="normal")


def holm(p_values: Sequence[float]) -> list[float]:
    """Holm–Bonferroni adjusted p-values, in the input order."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p_values[i]))
        adjusted[i] = running
    return adjusted


def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> float:
    """P(a > b) - P(a < b) over all pairs: how often one configuration's
    per-PR value beats the other's."""
    x = np.asarray(a, float)[:, None]
    y = np.asarray(b, float)[None, :]
    if x.size == 0 or y.size == 0:
        raise ValueError("cliffs_delta needs two non-empty samples")
    return float(((x > y).sum() - (x < y).sum()) / (x.size * y.size))


def cliffs_magnitude(delta: float) -> str:
    d = abs(delta)
    if d < 0.147:
        return "negligible"
    if d < 0.33:
        return "small"
    if d < 0.474:
        return "medium"
    return "large"
