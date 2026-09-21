"""Unit tests for `revu.context.traverse.bounded_expand` against small,
hand-built graphs - k-hop bounding and per-edge-kind toggling."""

import rustworkx as rx
from revu.context.traverse import bounded_expand, collect_edge_kinds


def _linear_graph() -> tuple[rx.PyDiGraph, list[int]]:
    """A -calls-> B -calls-> C -imports-> D (a straight line, mixed edge
    kinds), so hop distance and edge-kind filtering are both exercised
    independently."""
    graph: rx.PyDiGraph = rx.PyDiGraph()
    a = graph.add_node({"name": "A"})
    b = graph.add_node({"name": "B"})
    c = graph.add_node({"name": "C"})
    d = graph.add_node({"name": "D"})
    graph.add_edge(a, b, {"kind": "calls"})
    graph.add_edge(b, c, {"kind": "calls"})
    graph.add_edge(c, d, {"kind": "imports"})
    return graph, [a, b, c, d]


def test_seed_always_included_at_distance_zero_even_with_k_zero() -> None:
    graph, (a, _b, _c, _d) = _linear_graph()
    hits = bounded_expand(graph, [a], k=0, edge_kinds=frozenset({"calls", "imports"}))
    assert set(hits) == {a}
    assert hits[a].distance == 0
    assert hits[a].via_kind is None


def test_k_hop_bound_limits_expansion_distance() -> None:
    graph, (a, b, c, d) = _linear_graph()
    edge_kinds = frozenset({"calls", "imports"})

    hits_k1 = bounded_expand(graph, [a], k=1, edge_kinds=edge_kinds)
    assert set(hits_k1) == {a, b}

    hits_k2 = bounded_expand(graph, [a], k=2, edge_kinds=edge_kinds)
    assert set(hits_k2) == {a, b, c}
    assert hits_k2[c].distance == 2

    hits_k3 = bounded_expand(graph, [a], k=3, edge_kinds=edge_kinds)
    assert set(hits_k3) == {a, b, c, d}
    assert hits_k3[d].distance == 3


def test_edge_kind_toggle_excludes_edges_of_the_disabled_kind() -> None:
    graph, (a, b, c, d) = _linear_graph()
    # Only "calls" enabled: traversal can reach B and C but not D, since the
    # C -> D edge is "imports".
    hits = bounded_expand(graph, [a], k=5, edge_kinds=frozenset({"calls"}))
    assert set(hits) == {a, b, c}

    # Only "imports" enabled: A has no outgoing "imports" edge at all, so
    # nothing beyond the seed is reachable.
    hits_imports_only = bounded_expand(graph, [a], k=5, edge_kinds=frozenset({"imports"}))
    assert set(hits_imports_only) == {a}


def test_direction_callers_vs_callees() -> None:
    graph, (a, b, c, d) = _linear_graph()
    edge_kinds = frozenset({"calls", "imports"})

    # From B: "callees" (outgoing) direction reaches forward through C to D;
    # "callers" (incoming) direction reaches backward to A instead.
    callees = bounded_expand(graph, [b], k=5, edge_kinds=edge_kinds, direction="callees")
    assert set(callees) == {b, c, d}

    callers = bounded_expand(graph, [b], k=5, edge_kinds=edge_kinds, direction="callers")
    assert set(callers) == {a, b}

    both = bounded_expand(graph, [b], k=5, edge_kinds=edge_kinds, direction="both")
    assert set(both) == {a, b, c, d}


def test_shortest_distance_wins_on_diamond() -> None:
    """A -> B -> D and A -> C -> D: D should record distance 2 via whichever
    branch the BFS pops first, not overwritten by a longer path."""
    graph: rx.PyDiGraph = rx.PyDiGraph()
    a = graph.add_node({"name": "A"})
    b = graph.add_node({"name": "B"})
    c = graph.add_node({"name": "C"})
    d = graph.add_node({"name": "D"})
    graph.add_edge(a, b, {"kind": "calls"})
    graph.add_edge(a, c, {"kind": "calls"})
    graph.add_edge(b, d, {"kind": "calls"})
    graph.add_edge(c, d, {"kind": "calls"})

    hits = bounded_expand(graph, [a], k=5, edge_kinds=frozenset({"calls"}))
    assert hits[d].distance == 2


def test_collect_edge_kinds_returns_every_kind_present() -> None:
    graph, _nodes = _linear_graph()
    assert collect_edge_kinds(graph) == frozenset({"calls", "imports"})
