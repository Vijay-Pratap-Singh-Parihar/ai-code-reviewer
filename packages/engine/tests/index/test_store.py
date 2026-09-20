from pathlib import Path

import rustworkx as rx
from revu.index.store import load_graph, save_graph, to_branch_index_fields


def test_save_and_load_graph_round_trips(tmp_path: Path) -> None:
    graph: rx.PyDiGraph = rx.PyDiGraph()
    a = graph.add_node({"qualified_name": "pkg.a", "kind": "module"})
    b = graph.add_node({"qualified_name": "pkg.b", "kind": "module"})
    graph.add_edge(a, b, {"kind": "imports", "line": 1})

    path = save_graph(
        graph,
        repo_identifier="org/repo",
        branch_name="main",
        head_sha="abc123",
        storage_dir=tmp_path,
    )
    assert path.exists()
    assert path.parent == tmp_path

    loaded = load_graph(path)
    assert loaded.num_nodes() == 2
    assert loaded.num_edges() == 1
    assert loaded[a]["qualified_name"] == "pkg.a"
    assert loaded.get_edge_data(a, b)["kind"] == "imports"


def test_save_graph_is_deterministic_per_repo_branch_sha(tmp_path: Path) -> None:
    graph: rx.PyDiGraph = rx.PyDiGraph()
    path_1 = save_graph(
        graph, repo_identifier="org/repo", branch_name="main", head_sha="sha1", storage_dir=tmp_path
    )
    path_2 = save_graph(
        graph, repo_identifier="org/repo", branch_name="main", head_sha="sha1", storage_dir=tmp_path
    )
    path_3 = save_graph(
        graph, repo_identifier="org/repo", branch_name="main", head_sha="sha2", storage_dir=tmp_path
    )
    assert path_1 == path_2
    assert path_1 != path_3


def test_to_branch_index_fields_shapes_a_branch_index_row() -> None:
    fields = to_branch_index_fields(
        node_count=10,
        edge_count=20,
        graph_ref="/var/data/graph.pkl.gz",
        unresolved=[{"kind": "call", "reason": "test"}],
        build_duration_ms=1234,
    )
    assert fields == {
        "node_count": 10,
        "edge_count": 20,
        "graph_ref": "/var/data/graph.pkl.gz",
        "unresolved_symbols": [{"kind": "call", "reason": "test"}],
        "build_duration_ms": 1234,
    }


def test_to_branch_index_fields_caps_unresolved_list() -> None:
    unresolved = [{"i": i} for i in range(10)]
    fields = to_branch_index_fields(
        node_count=0,
        edge_count=0,
        graph_ref="x",
        unresolved=unresolved,
        build_duration_ms=0,
        max_unresolved_logged=3,
    )
    assert len(fields["unresolved_symbols"]) == 3
