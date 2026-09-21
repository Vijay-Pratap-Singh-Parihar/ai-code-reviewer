"""Unit tests for `revu.context.mapping` against small, hand-built graphs
(no real indexed repo needed for these)."""

import rustworkx as rx
from revu.context.diff import parse_diff
from revu.context.mapping import map_diff_to_nodes, map_range_to_nodes
from revu.index.types import SymbolKind


def _module_node(file_path: str, qualified_name: str) -> dict[str, object]:
    return {
        "qualified_name": qualified_name,
        "kind": SymbolKind.MODULE.value,
        "file_path": file_path,
        "line_start": 1,
        "line_end": 1,
    }


def _symbol_node(
    file_path: str, qualified_name: str, kind: SymbolKind, line_start: int, line_end: int
) -> dict[str, object]:
    return {
        "qualified_name": qualified_name,
        "kind": kind.value,
        "file_path": file_path,
        "line_start": line_start,
        "line_end": line_end,
    }


def _build_graph() -> tuple[rx.PyDiGraph, dict[str, int]]:
    """One file `pkg/mod.py` with a module node and two functions
    (`foo`: lines 1-10, `bar`: lines 12-20), and a second, unrelated file
    `pkg/other.py` with just a module node.
    """
    graph: rx.PyDiGraph = rx.PyDiGraph()
    nodes: dict[str, int] = {}
    nodes["module"] = graph.add_node(_module_node("pkg/mod.py", "pkg.mod"))
    nodes["foo"] = graph.add_node(
        _symbol_node("pkg/mod.py", "pkg.mod.foo", SymbolKind.FUNCTION, 1, 10)
    )
    nodes["bar"] = graph.add_node(
        _symbol_node("pkg/mod.py", "pkg.mod.bar", SymbolKind.FUNCTION, 12, 20)
    )
    nodes["other_module"] = graph.add_node(_module_node("pkg/other.py", "pkg.other"))
    return graph, nodes


def test_range_fully_inside_one_symbol_maps_to_that_symbol() -> None:
    graph, nodes = _build_graph()
    file_index = {"pkg/mod.py": [nodes["module"], nodes["foo"], nodes["bar"]]}
    node_indices, matched_module_only = map_range_to_nodes(
        graph, file_index, file_path="pkg/mod.py", line_start=3, line_end=5
    )
    assert node_indices == (nodes["foo"],)
    assert matched_module_only is False


def test_range_spanning_two_symbols_maps_to_both() -> None:
    graph, nodes = _build_graph()
    file_index = {"pkg/mod.py": [nodes["module"], nodes["foo"], nodes["bar"]]}
    node_indices, matched_module_only = map_range_to_nodes(
        graph, file_index, file_path="pkg/mod.py", line_start=8, line_end=14
    )
    assert set(node_indices) == {nodes["foo"], nodes["bar"]}
    assert matched_module_only is False


def test_range_with_no_enclosing_symbol_falls_back_to_module_node() -> None:
    graph, nodes = _build_graph()
    file_index = {"pkg/mod.py": [nodes["module"], nodes["foo"], nodes["bar"]]}
    # Line 11 is the gap between foo (1-10) and bar (12-20): module-level.
    node_indices, matched_module_only = map_range_to_nodes(
        graph, file_index, file_path="pkg/mod.py", line_start=11, line_end=11
    )
    assert node_indices == (nodes["module"],)
    assert matched_module_only is True


def test_range_in_an_unindexed_file_maps_to_nothing() -> None:
    graph, nodes = _build_graph()
    file_index = {"pkg/mod.py": [nodes["module"], nodes["foo"], nodes["bar"]]}
    node_indices, matched_module_only = map_range_to_nodes(
        graph, file_index, file_path="pkg/not_indexed.py", line_start=1, line_end=5
    )
    assert node_indices == ()
    assert matched_module_only is False


def test_map_diff_to_nodes_end_to_end() -> None:
    graph, nodes = _build_graph()
    diff_text = (
        "--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -3,1 +3,1 @@\n-old\n+new\n"
    )
    file_diffs = parse_diff(diff_text)
    mapped = map_diff_to_nodes(graph, file_diffs)
    assert len(mapped) == 1
    assert mapped[0].file_path == "pkg/mod.py"
    assert mapped[0].node_indices == (nodes["foo"],)
    assert mapped[0].matched_module_only is False
