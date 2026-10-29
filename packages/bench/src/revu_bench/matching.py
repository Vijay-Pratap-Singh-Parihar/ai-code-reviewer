"""Matching predicted findings to ground-truth review comments (§10.2).

A match is one-to-one: one finding can explain at most one ground-truth
comment and vice versa, so a system can't raise its recall by repeating the
same finding. Among all valid pairings the matcher finds a *maximum* one
(augmenting paths over the compatibility graph), so the score doesn't
depend on the order findings were emitted in, and it prefers the closest
lines when there's a choice.

Three modes:

- `location`: same file, line ranges overlap within `tolerance` lines.
  Deterministic and free; the default for development and sweeps.
- `strict`: `location` **and** a judge agrees it is the same issue.
- `lenient`: same file **and** the judge agrees, wherever the lines are.

The judge is any callable (typically an LLM from a different model family
than the reviewer, validated against human labels; §10.2). It is only asked
about pairs that are already plausible, which keeps its cost down.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from revu_bench.schema import GroundTruthComment, PredictedFinding

MatchMode = Literal["location", "strict", "lenient"]
Judge = Callable[[PredictedFinding, GroundTruthComment], bool]

DEFAULT_TOLERANCE = 3


def normalize_path(path: str) -> str:
    p = path.strip().replace("\\", "/")
    for prefix in ("a/", "b/", "./"):
        if p.startswith(prefix):
            p = p[len(prefix) :]
    return p.lstrip("/")


def line_gap(f: PredictedFinding, g: GroundTruthComment) -> int:
    """0 when the ranges overlap, otherwise the number of lines between them."""
    if f.line_end < g.line_start:
        return g.line_start - f.line_end
    if g.line_end < f.line_start:
        return f.line_start - g.line_end
    return 0


@dataclass(frozen=True)
class MatchResult:
    pairs: list[tuple[int, int]]  # (finding index, ground-truth index)
    n_findings: int
    n_ground_truth: int

    @property
    def matched_findings(self) -> set[int]:
        return {f for f, _ in self.pairs}

    @property
    def matched_ground_truth(self) -> set[int]:
        return {g for _, g in self.pairs}

    @property
    def tp(self) -> int:
        return len(self.pairs)

    @property
    def fp(self) -> int:
        return self.n_findings - len(self.pairs)

    @property
    def fn(self) -> int:
        return self.n_ground_truth - len(self.pairs)


def match(
    findings: Sequence[PredictedFinding],
    ground_truth: Sequence[GroundTruthComment],
    *,
    mode: MatchMode = "location",
    tolerance: int = DEFAULT_TOLERANCE,
    judge: Judge | None = None,
) -> MatchResult:
    if mode != "location" and judge is None:
        raise ValueError(f"mode {mode!r} needs a judge")

    # Candidate edges per finding, closest first.
    edges: list[list[int]] = []
    for f in findings:
        f_path = normalize_path(f.path)
        candidates: list[tuple[int, int]] = []
        for gi, g in enumerate(ground_truth):
            if normalize_path(g.path) != f_path:
                continue
            gap = line_gap(f, g)
            if mode in ("location", "strict") and gap > tolerance:
                continue
            candidates.append((gap, gi))
        candidates.sort()
        edges.append([gi for _, gi in candidates])

    if judge is not None and mode != "location":
        edges = [
            [gi for gi in options if judge(findings[fi], ground_truth[gi])]
            for fi, options in enumerate(edges)
        ]

    owner: dict[int, int] = {}  # ground-truth index -> finding index

    def augment(fi: int, seen: set[int]) -> bool:
        for gi in edges[fi]:
            if gi in seen:
                continue
            seen.add(gi)
            if gi not in owner or augment(owner[gi], seen):
                owner[gi] = fi
                return True
        return False

    for fi in range(len(findings)):
        augment(fi, set())

    pairs = sorted((fi, gi) for gi, fi in owner.items())
    return MatchResult(pairs=pairs, n_findings=len(findings), n_ground_truth=len(ground_truth))
