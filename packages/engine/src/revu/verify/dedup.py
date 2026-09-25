"""Fuzzy-match findings that likely describe the same underlying issue and
merge them into one.

Two findings are considered the same issue if they share a **file path**, a
**category**, and their line ranges **overlap or are within
`line_gap_tolerance` lines of each other**. This is deliberately simple
(no semantic/embedding similarity - forbidden this stage anyway, see
`IMPLEMENTATION_PLAN.md` Stage 8) but is exactly the roadmap's own spec for
Phase 6's `dedup.py`: "fuzzy match on file, overlapping range, category;
merge messages."

**Why `MergedFinding`, not a plain `list[Finding]`.** `revu.models.Finding`
has a single `agent_name: str` field - it was never designed to carry "this
finding is actually N agents agreeing." Stage 8's confidence scoring
(`verify/confidence.py`) needs exactly that count (agreement across
independent findings is a stronger signal than one lone claim), so this
module returns a `MergedFinding` wrapper carrying the merged `Finding`
alongside `source_count` and the original findings' own `confidence`/
`agent_name` values - the metadata that would otherwise be destroyed by
collapsing N findings into one.

**The `agent_name` merge convention** (a judgment call, documented here
since the task explicitly leaves it open): the merged finding's
`agent_name` is the sorted, de-duplicated set of contributing agent names
joined with `"+"` (e.g. `"cross_file+diff_only"`). A single-agent cluster
(including one agent producing two overlapping findings in one run) keeps
that one name unchanged - no redundant self-joining. This was chosen over
"keep the first" because the joined string is directly useful to a reader
(the UI can show "flagged by cross_file+diff_only" as a visible agreement
signal), and over inventing a new field because it keeps `Finding`'s shape
stable for every other caller.
"""

from __future__ import annotations

from dataclasses import dataclass

from revu.models import EvidenceItem, Finding, FindingCategory, Severity

_SEVERITY_RANK: dict[Severity, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}

DEFAULT_LINE_GAP_TOLERANCE = 2


@dataclass(frozen=True)
class MergedFinding:
    """The result of merging one or more `Finding`s that were judged to
    describe the same underlying issue.

    `source_count == 1` for a finding that matched nothing else - dedup is a
    no-op for it, but it still gets wrapped so callers have one uniform type
    to carry through the rest of the pipeline.
    """

    finding: Finding
    source_count: int
    source_agent_names: tuple[str, ...]
    source_confidences: tuple[float, ...]


def _ranges_overlap_or_close(
    start_a: int, end_a: int, start_b: int, end_b: int, *, tolerance: int
) -> bool:
    return start_a <= end_b + tolerance and start_b <= end_a + tolerance


def _combine_messages(messages: list[str]) -> str:
    """Merge N findings' messages into one, preserving every distinct
    wording rather than picking one arbitrarily and discarding the rest -
    per the roadmap's explicit instruction ("merge messages").
    """
    if len(messages) == 1:
        return messages[0]
    unique = list(dict.fromkeys(messages))  # de-dupe exact-identical text, preserve order
    if len(unique) == 1:
        return unique[0]
    numbered = "\n".join(f"{i}. {msg}" for i, msg in enumerate(unique, start=1))
    return f"Corroborated by {len(unique)} independent findings:\n{numbered}"


def _combine_evidence(evidence_lists: list[list[EvidenceItem]]) -> list[EvidenceItem]:
    combined: list[EvidenceItem] = [item for items in evidence_lists for item in items]
    return list(dict.fromkeys(combined))  # de-dupe exact duplicates, preserve order


def _merge_agent_name(agent_names: list[str]) -> str:
    unique = sorted(set(agent_names))
    return "+".join(unique)


def _merge_cluster(cluster: list[Finding]) -> MergedFinding:
    if len(cluster) == 1:
        only = cluster[0]
        return MergedFinding(
            finding=only,
            source_count=1,
            source_agent_names=(only.agent_name,),
            source_confidences=(only.confidence,),
        )

    file_path = cluster[0].file_path
    category = cluster[0].category
    line_start = min(f.line_start for f in cluster)
    line_end = max(f.line_end for f in cluster)
    severity = max((f.severity for f in cluster), key=lambda s: _SEVERITY_RANK[s])
    message = _combine_messages([f.message for f in cluster])
    evidence = _combine_evidence([f.evidence for f in cluster])
    agent_names = [f.agent_name for f in cluster]
    confidences = [f.confidence for f in cluster]
    # Placeholder confidence: the max of the sources'. `verify.confidence`
    # recomputes this from scratch (own confidence + evidence survival +
    # agreement) once evidence resolution has run - this value only matters
    # if `dedup_findings` is used standalone, without the rest of the
    # pipeline.
    confidence = max(confidences)

    merged = Finding(
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        category=category,
        severity=severity,
        message=message,
        evidence=evidence,
        confidence=confidence,
        agent_name=_merge_agent_name(agent_names),
    )
    return MergedFinding(
        finding=merged,
        source_count=len(cluster),
        source_agent_names=tuple(agent_names),
        source_confidences=tuple(confidences),
    )


@dataclass
class _Indexed:
    index: int
    finding: Finding


def dedup_findings(
    findings: list[Finding], *, line_gap_tolerance: int = DEFAULT_LINE_GAP_TOLERANCE
) -> list[MergedFinding]:
    """Cluster `findings` by (file_path, category, overlapping/close line
    range) and merge each cluster into one `MergedFinding`.

    Designed to operate on findings from potentially multiple source agents
    at once (per the architecture doc's multi-agent aggregation framing),
    even though today's only callers produce one agent's findings at a time.

    Output order: clusters are returned in the order their earliest-input
    member first appeared, so a caller with a single agent's findings sees
    a stable, input-order-like result.
    """
    groups: dict[tuple[str, FindingCategory], list[_Indexed]] = {}
    for i, finding in enumerate(findings):
        key = (finding.file_path, finding.category)
        groups.setdefault(key, []).append(_Indexed(index=i, finding=finding))

    clusters: list[tuple[int, list[Finding]]] = []  # (min_original_index, cluster)
    for items in groups.values():
        items.sort(key=lambda item: (item.finding.line_start, item.index))
        current_cluster: list[_Indexed] = []
        current_end = -1
        for item in items:
            if current_cluster and _ranges_overlap_or_close(
                current_cluster[0].finding.line_start,
                current_end,
                item.finding.line_start,
                item.finding.line_end,
                tolerance=line_gap_tolerance,
            ):
                current_cluster.append(item)
                current_end = max(current_end, item.finding.line_end)
            else:
                if current_cluster:
                    clusters.append(
                        (
                            min(c.index for c in current_cluster),
                            [c.finding for c in current_cluster],
                        )
                    )
                current_cluster = [item]
                current_end = item.finding.line_end
        if current_cluster:
            clusters.append(
                (min(c.index for c in current_cluster), [c.finding for c in current_cluster])
            )

    clusters.sort(key=lambda pair: pair[0])
    return [_merge_cluster(cluster) for _, cluster in clusters]


__all__ = ["MergedFinding", "dedup_findings", "DEFAULT_LINE_GAP_TOLERANCE"]
