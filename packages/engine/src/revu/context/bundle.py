"""Assemble a `revu.models.ContextBundle` for a diff against an indexed
graph: diff -> hunk/node mapping -> bounded k-hop traversal -> ranking ->
token-budgeted selection -> `ContextItem`s with a human-readable
`retrieval_reason` each.

`build_context_bundle` is the single entry point later stages (Stage 7) are
expected to call, mirroring `revu.agents.diff_only.review_diff`'s shape: one
function, plain keyword configuration, a `revu.models` return type.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import rustworkx as rx

from revu.context.budget import BudgetCandidate, count_tokens, select_within_budget
from revu.context.diff import parse_diff
from revu.context.mapping import map_diff_to_nodes
from revu.context.rank import EmbeddingScorer, RankCandidate, RankWeights, rank_candidates
from revu.context.traverse import Direction, TraversalHit, bounded_expand
from revu.index.types import EdgeKind, SymbolKind
from revu.models import ContextBundle, ContextItem

logger = logging.getLogger(__name__)

DEFAULT_EDGE_KINDS: frozenset[str] = frozenset({EdgeKind.IMPORTS.value, EdgeKind.CALLS.value})

# A MODULE node's own line range is always the degenerate (1, 1) - Stage 4's
# graph never tracked a module's real extent, only symbols within it (see
# `revu.index.graph.assemble_graph`). Rather than reading the whole file
# (unbounded, could blow the token budget on a single large file) or the
# literal single line (near-useless as context), a related module reached by
# traversal is previewed as its first `_MODULE_PREVIEW_LINES` lines - usually
# the docstring/import block, which is the most representative fixed-size
# slice available without deeper Stage 4 changes.
_MODULE_PREVIEW_LINES = 20


@dataclass(frozen=True)
class RetrievalConfig:
    k: int = 2
    token_budget: int = 8000
    edge_kinds: frozenset[str] = field(default_factory=lambda: DEFAULT_EDGE_KINDS)
    direction: Direction = "both"
    weights: RankWeights = field(default_factory=RankWeights)
    embedding_scorer: EmbeddingScorer | None = None


@dataclass(frozen=True)
class _Candidate:
    candidate_id: int
    file_path: str
    line_start: int
    line_end: int
    content: str
    distance: int
    hit: TraversalHit | None
    is_raw_hunk: bool


def _read_file_lines(repo_root: Path, file_path: str) -> list[str] | None:
    try:
        text = (repo_root / file_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("context bundle: could not read %s: %s", file_path, exc)
        return None
    return text.splitlines()


def _content_for_range(
    repo_root: Path, file_path: str, line_start: int, line_end: int
) -> tuple[str, int, int] | None:
    lines = _read_file_lines(repo_root, file_path)
    if lines is None:
        return None
    start = max(line_start, 1)
    end = min(line_end, len(lines))
    if end < start:
        return None
    return "\n".join(lines[start - 1 : end]), start, end


def _content_for_node(repo_root: Path, node: dict[str, object]) -> tuple[str, int, int] | None:
    file_path = str(node["file_path"])
    if node["kind"] == SymbolKind.MODULE.value:
        lines = _read_file_lines(repo_root, file_path)
        if lines is None:
            return None
        end = min(_MODULE_PREVIEW_LINES, len(lines))
        if end <= 0:
            return None
        return "\n".join(lines[0:end]), 1, end
    line_start, line_end = node["line_start"], node["line_end"]
    assert isinstance(line_start, int) and isinstance(line_end, int)
    return _content_for_range(repo_root, file_path, line_start, line_end)


def _direction_word(direction: str | None) -> str:
    if direction == "callees":
        return "callee"
    if direction == "callers":
        return "caller"
    return "related"


def _reason_for(candidate: _Candidate, graph: rx.PyDiGraph, *, rank: int) -> str:
    if candidate.is_raw_hunk:
        base = "directly changed lines (no enclosing function/class found)"
    elif candidate.hit is not None and candidate.hit.distance == 0:
        qualified_name = graph[candidate.candidate_id]["qualified_name"]
        base = f"directly changed symbol `{qualified_name}`"
    else:
        hit = candidate.hit
        assert hit is not None and hit.from_node_index is not None
        from_node = graph[hit.from_node_index]
        base = (
            f"{hit.distance}-hop {_direction_word(hit.via_direction)} via `{hit.via_kind}` "
            f"edge from `{from_node['qualified_name']}`"
        )
    return f"{base}, ranked #{rank}"


def build_context_bundle(
    diff_text: str,
    graph: rx.PyDiGraph,
    repo_root: Path,
    *,
    config: RetrievalConfig | None = None,
) -> ContextBundle:
    """Build a `ContextBundle` for `diff_text` against `graph` (a Stage 4
    `IndexResult.graph`, or one loaded via `revu.index.load_graph`).

    `repo_root` must be a working tree whose files match the `file_path`s
    the graph's nodes were built from (the same root passed to
    `build_index`/`build_index_at_path`) - content is read from disk, not
    stored in the graph itself.
    """
    cfg = config or RetrievalConfig()

    file_diffs = parse_diff(diff_text)
    mapped_hunks = map_diff_to_nodes(graph, file_diffs)

    seed_indices: set[int] = set()
    raw_hunk_specs: list[tuple[str, int, int]] = []
    for mapped in mapped_hunks:
        if mapped.node_indices and not mapped.matched_module_only:
            seed_indices.update(mapped.node_indices)
        else:
            # Module-level hunk (no enclosing symbol) or a file the indexer
            # doesn't know about at all: the enclosing MODULE node's own line
            # range is a degenerate (1, 1) placeholder (see
            # `_MODULE_PREVIEW_LINES` above), so it's not usable as *content*
            # for this specific hunk - fall back to the literal touched
            # lines. The module node (if one exists) is still used as a
            # traversal seed, so import-graph expansion from it still runs.
            seed_indices.update(mapped.node_indices)
            raw_hunk_specs.append((mapped.file_path, mapped.line_start, mapped.line_end))

    traversal_hits = bounded_expand(
        graph, seed_indices, k=cfg.k, edge_kinds=cfg.edge_kinds, direction=cfg.direction
    )

    candidates: list[_Candidate] = []
    for node_index, hit in traversal_hits.items():
        node = graph[node_index]
        is_redundant_module_seed = (
            hit.distance == 0 and node["kind"] == SymbolKind.MODULE.value
        )
        if is_redundant_module_seed:
            # Covered by a raw-hunk candidate instead (see above) - avoid
            # duplicating the same location as two separate context items.
            continue
        content_info = _content_for_node(repo_root, node)
        if content_info is None:
            continue
        content, line_start, line_end = content_info
        candidates.append(
            _Candidate(
                candidate_id=node_index,
                file_path=node["file_path"],
                line_start=line_start,
                line_end=line_end,
                content=content,
                distance=hit.distance,
                hit=hit,
                is_raw_hunk=False,
            )
        )

    next_synthetic_id = -1
    for file_path, line_start, line_end in raw_hunk_specs:
        content_info = _content_for_range(repo_root, file_path, line_start, line_end)
        if content_info is None:
            continue
        content, resolved_start, resolved_end = content_info
        candidates.append(
            _Candidate(
                candidate_id=next_synthetic_id,
                file_path=file_path,
                line_start=resolved_start,
                line_end=resolved_end,
                content=content,
                distance=0,
                hit=None,
                is_raw_hunk=True,
            )
        )
        next_synthetic_id -= 1

    ranked = rank_candidates(
        [
            RankCandidate(candidate_id=c.candidate_id, content=c.content, distance=c.distance)
            for c in candidates
        ],
        diff_text=diff_text,
        weights=cfg.weights,
        embedding_scorer=cfg.embedding_scorer,
    )
    score_by_id = {r.candidate_id: r.score for r in ranked}

    budget_candidates = [
        BudgetCandidate(
            candidate_id=c.candidate_id,
            token_count=count_tokens(c.content),
            score=score_by_id[c.candidate_id],
        )
        for c in candidates
    ]
    selection = select_within_budget(budget_candidates, budget=cfg.token_budget)
    selected_ids = set(selection.selected_ids)

    candidates_by_id = {c.candidate_id: c for c in candidates}
    ordered_selected = [r for r in ranked if r.candidate_id in selected_ids]

    items: list[ContextItem] = [
        ContextItem(
            file_path=candidates_by_id[ranked_candidate.candidate_id].file_path,
            line_start=candidates_by_id[ranked_candidate.candidate_id].line_start,
            line_end=candidates_by_id[ranked_candidate.candidate_id].line_end,
            content=candidates_by_id[ranked_candidate.candidate_id].content,
            retrieval_reason=_reason_for(
                candidates_by_id[ranked_candidate.candidate_id], graph, rank=rank
            ),
            score=ranked_candidate.score,
        )
        for rank, ranked_candidate in enumerate(ordered_selected, start=1)
    ]

    strategy = (
        f"k={cfg.k}-hop {cfg.direction} traversal over {{{', '.join(sorted(cfg.edge_kinds))}}} "
        f"edges, ranked by {cfg.weights.graph_distance}*graph_distance + "
        f"{cfg.weights.bm25}*bm25 + {cfg.weights.embedding}*embedding, budgeted to "
        f"{cfg.token_budget} tokens (tiktoken cl100k_base)"
    )

    return ContextBundle(
        items=items, total_tokens=selection.total_tokens, retrieval_strategy=strategy
    )
