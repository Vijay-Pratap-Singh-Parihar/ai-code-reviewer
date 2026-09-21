"""Bounded k-hop expansion from a set of "changed" graph nodes.

Every edge kind the indexer tags (`imports`, `calls` - see
`revu.index.types.EdgeKind`) is individually toggleable via `edge_kinds`,
per the roadmap's explicit instruction ("this gives you ablations for
free"): a caller can restrict traversal to only `calls` edges, only
`imports` edges, or any future edge kind Stage 4 adds later, without any
code change here.

Direction matters for change-impact analysis: expanding along outgoing
edges from a changed node finds what it *depends on* (its callees/imports -
useful for understanding the change itself); expanding along incoming edges
finds what *depends on it* (its callers/importers - what could break).
`direction="both"` (the default) explores both.

Implemented as a manual BFS rather than `rustworkx.bfs_search` because the
traversal needs to filter by edge payload (`kind`) and combine both edge
directions under one hop budget, which `rustworkx`'s built-in search
visitors don't support directly.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

import rustworkx as rx

Direction = Literal["callers", "callees", "both"]


@dataclass(frozen=True)
class TraversalHit:
    """One node reached by the traversal, with how it was first (shortest)
    reached. `distance == 0` means it was a seed node (directly changed);
    `via_kind`/`via_direction`/`from_node_index` are `None` in that case.
    """

    node_index: int
    distance: int
    via_kind: str | None
    via_direction: Direction | None
    from_node_index: int | None


def bounded_expand(
    graph: rx.PyDiGraph,
    seed_nodes: Iterable[int],
    *,
    k: int,
    edge_kinds: frozenset[str],
    direction: Direction = "both",
) -> dict[int, TraversalHit]:
    """BFS up to `k` hops from `seed_nodes`, following only edges whose
    `kind` payload is in `edge_kinds`, in the requested direction(s).

    A node reachable via more than one path keeps the shortest distance it
    was first discovered at (first-discovery wins, standard BFS semantics).
    Seed nodes themselves are always included at distance 0, even if `k == 0`.
    """
    if k < 0:
        raise ValueError("k must be >= 0")

    hits: dict[int, TraversalHit] = {}
    queue: deque[int] = deque()

    for seed in seed_nodes:
        if seed not in hits:
            hits[seed] = TraversalHit(
                node_index=seed, distance=0, via_kind=None, via_direction=None, from_node_index=None
            )
            queue.append(seed)

    while queue:
        current = queue.popleft()
        current_distance = hits[current].distance
        if current_distance >= k:
            continue

        neighbours: list[tuple[int, str, Direction]] = []
        if direction in ("callees", "both"):
            for _source, target, data in graph.out_edges(current):
                kind = data.get("kind")
                if kind in edge_kinds:
                    neighbours.append((target, kind, "callees"))
        if direction in ("callers", "both"):
            for source, _target, data in graph.in_edges(current):
                kind = data.get("kind")
                if kind in edge_kinds:
                    neighbours.append((source, kind, "callers"))

        for neighbour, kind, via_direction in neighbours:
            if neighbour in hits:
                continue
            hits[neighbour] = TraversalHit(
                node_index=neighbour,
                distance=current_distance + 1,
                via_kind=kind,
                via_direction=via_direction,
                from_node_index=current,
            )
            queue.append(neighbour)

    return hits


def collect_edge_kinds(graph: rx.PyDiGraph) -> frozenset[str]:
    """Every distinct edge `kind` actually present in `graph` - a
    convenience for callers that want "all edge kinds" without hard-coding
    `revu.index.types.EdgeKind`'s current members (so a future new edge kind
    is picked up automatically).
    """
    kinds: set[str] = set()
    for edge_index in graph.edge_indices():
        data = graph.get_edge_data_by_index(edge_index)
        kind = data.get("kind")
        if kind is not None:
            kinds.add(kind)
    return frozenset(kinds)


def as_edge_kinds(
    kinds: Sequence[str] | frozenset[str] | None, graph: rx.PyDiGraph
) -> frozenset[str]:
    """Normalise a caller-supplied edge-kind selection: `None` means "every
    kind present in this graph".
    """
    if kinds is None:
        return collect_edge_kinds(graph)
    return frozenset(kinds)
