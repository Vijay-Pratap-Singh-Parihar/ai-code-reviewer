import rustworkx as rx
from revu.agents.tools.graph_query import graph_query


def test_unknown_symbol_returns_not_found(sample_graph: rx.PyDiGraph) -> None:
    result = graph_query(sample_graph, "does.not.exist")
    assert result.found is False


def test_found_node_reports_its_own_location(sample_graph: rx.PyDiGraph) -> None:
    result = graph_query(sample_graph, "mod_b.bar")
    assert result.found is True
    assert result.kind == "function"
    assert result.file_path == "mod_b.py"
    assert (result.line_start, result.line_end) == (10, 15)


def test_outgoing_edges_are_what_the_symbol_calls_or_imports(sample_graph: rx.PyDiGraph) -> None:
    result = graph_query(sample_graph, "mod_b.bar")
    assert len(result.outgoing) == 1
    assert result.outgoing[0].qualified_name == "mod_c.baz"
    assert result.outgoing[0].kind == "calls"
    assert result.outgoing[0].at_line == 12


def test_incoming_edges_are_what_calls_or_imports_the_symbol(sample_graph: rx.PyDiGraph) -> None:
    result = graph_query(sample_graph, "mod_b.bar")
    assert len(result.incoming) == 1
    assert result.incoming[0].qualified_name == "mod_a.foo"
    assert result.incoming[0].kind == "calls"


def test_module_node_import_edge(sample_graph: rx.PyDiGraph) -> None:
    result = graph_query(sample_graph, "mod_a")
    assert result.found is True
    assert result.kind == "module"
    assert any(e.qualified_name == "mod_b" and e.kind == "imports" for e in result.outgoing)


def test_multiple_edges_between_same_pair_are_not_collapsed(sample_graph: rx.PyDiGraph) -> None:
    # Add a second call from foo to bar at a different line.
    foo_idx = next(
        i for i in sample_graph.node_indices() if sample_graph[i]["qualified_name"] == "mod_a.foo"
    )
    bar_idx = next(
        i for i in sample_graph.node_indices() if sample_graph[i]["qualified_name"] == "mod_b.bar"
    )
    sample_graph.add_edge(foo_idx, bar_idx, {"kind": "calls", "line": 5})

    result = graph_query(sample_graph, "mod_a.foo")
    calls_to_bar = [e for e in result.outgoing if e.qualified_name == "mod_b.bar"]
    assert len(calls_to_bar) == 2
    assert {e.at_line for e in calls_to_bar} == {4, 5}
