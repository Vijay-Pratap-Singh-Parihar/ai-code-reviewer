"""Blast radius: which code a pull request's changes can affect.

The changed symbols are the graph nodes the diff's hunks land on (functions,
methods, classes; a hunk outside any symbol isn't counted). Their blast
radius is every symbol that calls them, directly or through up to `depth`
calls, found with the same bounded traversal the context retrieval uses
(`revu.context.traverse.bounded_expand`). No model is involved: it is read
off the branch's prebuilt graph.

The result is plain JSON for the run page's graph: the nodes (changed ones
at distance 0, callers at their call distance), the `calls` edges among
them, and counts. Large radii are cut to `max_nodes`, nearest first.
"""

from __future__ import annotations

from typing import Any

import rustworkx as rx

from revu.context.diff import parse_diff
from revu.context.mapping import map_diff_to_nodes
from revu.context.traverse import bounded_expand

__all__ = ["DEFAULT_DEPTH", "DEFAULT_MAX_NODES", "blast_radius"]

DEFAULT_DEPTH = 2
DEFAULT_MAX_NODES = 120
_CALLS = frozenset({"calls"})


def blast_radius(
    graph: rx.PyDiGraph,
    diff_text: str,
    *,
    depth: int = DEFAULT_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> dict[str, Any]:
    hunks = map_diff_to_nodes(graph, parse_diff(diff_text))
    seeds = sorted(
        {idx for hunk in hunks if not hunk.matched_module_only for idx in hunk.node_indices}
    )
    hits = bounded_expand(graph, seeds, k=depth, edge_kinds=_CALLS, direction="callers")

    def nearest_first(hit: Any) -> tuple[int, str, int]:
        node = graph[hit.node_index]
        return (hit.distance, node["file_path"], node["line_start"])

    ordered = sorted(hits.values(), key=nearest_first)
    kept = ordered[:max_nodes]
    kept_ids = {h.node_index for h in kept}
    changed_files = {graph[idx]["file_path"] for idx in seeds}

    nodes = [
        {
            "id": h.node_index,
            "name": graph[h.node_index]["qualified_name"],
            "kind": graph[h.node_index]["kind"],
            "file_path": graph[h.node_index]["file_path"],
            "line_start": graph[h.node_index]["line_start"],
            "line_end": graph[h.node_index]["line_end"],
            "distance": h.distance,
        }
        for h in kept
    ]
    # Every call among the kept nodes, not only the traversal's tree.
    edges = sorted(
        {
            (source, target)
            for source, target, data in graph.weighted_edge_list()
            if data.get("kind") in _CALLS and source in kept_ids and target in kept_ids
        }
    )
    impacted = [h for h in hits.values() if h.distance > 0]
    return {
        "depth": depth,
        "changed": len(seeds),
        "impacted": len(impacted),
        "impacted_files": len({graph[h.node_index]["file_path"] for h in impacted} - changed_files),
        "truncated": len(ordered) > max_nodes,
        "nodes": nodes,
        "edges": [{"caller": s, "callee": t} for s, t in edges],
    }
