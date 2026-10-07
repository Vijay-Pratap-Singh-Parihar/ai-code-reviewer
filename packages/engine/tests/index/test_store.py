from pathlib import Path

import pytest
import rustworkx as rx
from revu.index.graph import IndexResult, build_index_at_path
from revu.index.store import (
    IndexIntegrityError,
    index_result_path,
    load_graph,
    load_index_result,
    save_graph,
    save_index_result,
    to_branch_index_fields,
)

KEY = b"k" * 32


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
        signing_key=KEY,
    )
    assert path.exists()
    assert path.parent == tmp_path

    loaded = load_graph(path, signing_key=KEY)
    assert loaded.num_nodes() == 2
    assert loaded.num_edges() == 1
    assert loaded[a]["qualified_name"] == "pkg.a"
    assert loaded.get_edge_data(a, b)["kind"] == "imports"


def test_save_graph_is_deterministic_per_repo_branch_sha(tmp_path: Path) -> None:
    graph: rx.PyDiGraph = rx.PyDiGraph()
    path_1 = save_graph(
        graph,
        repo_identifier="org/repo",
        branch_name="main",
        head_sha="sha1",
        storage_dir=tmp_path,
        signing_key=KEY,
    )
    path_2 = save_graph(
        graph,
        repo_identifier="org/repo",
        branch_name="main",
        head_sha="sha1",
        storage_dir=tmp_path,
        signing_key=KEY,
    )
    path_3 = save_graph(
        graph,
        repo_identifier="org/repo",
        branch_name="main",
        head_sha="sha2",
        storage_dir=tmp_path,
        signing_key=KEY,
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


def test_save_and_load_index_result_round_trips(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def f():\n    return g()\n\n\ndef g():\n    return 1\n")
    result = build_index_at_path(tmp_path)

    path = save_index_result(
        result,
        repo_identifier="org/repo",
        branch_name="main",
        head_sha="a" * 40,
        storage_dir=tmp_path / ".blobs",
        signing_key=KEY,
    )
    assert path.exists()
    assert path.name.endswith(".result.pkl.gz")

    loaded = load_index_result(path, signing_key=KEY)
    assert isinstance(loaded, IndexResult)
    assert {s.qualified_name for s in loaded.symbols} == {s.qualified_name for s in result.symbols}
    assert {(c.caller, c.callee) for c in loaded.call_edges} == {
        (c.caller, c.callee) for c in result.call_edges
    }
    assert loaded.node_count == result.node_count


def test_index_result_path_matches_save_index_result(tmp_path: Path) -> None:
    result = build_index_at_path(tmp_path)
    saved_path = save_index_result(
        result,
        repo_identifier="org/repo",
        branch_name="main",
        head_sha="deadbeef",
        storage_dir=tmp_path / ".blobs",
        signing_key=KEY,
    )
    computed_path = index_result_path(
        repo_identifier="org/repo",
        branch_name="main",
        head_sha="deadbeef",
        storage_dir=tmp_path / ".blobs",
    )
    assert saved_path == computed_path


def test_load_index_result_rejects_a_graph_only_blob(tmp_path: Path) -> None:
    graph: rx.PyDiGraph = rx.PyDiGraph()
    graph_path = save_graph(
        graph,
        repo_identifier="org/repo",
        branch_name="main",
        head_sha="x",
        storage_dir=tmp_path,
        signing_key=KEY,
    )
    with pytest.raises(TypeError):
        load_index_result(graph_path, signing_key=KEY)


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


def _saved_graph(tmp_path: Path) -> Path:
    graph: rx.PyDiGraph = rx.PyDiGraph()
    graph.add_node({"qualified_name": "pkg.a"})
    return save_graph(
        graph,
        repo_identifier="r",
        branch_name="main",
        head_sha="s",
        storage_dir=tmp_path,
        signing_key=KEY,
    )


def test_load_graph_refuses_a_blob_signed_with_another_key(tmp_path: Path) -> None:
    """A blob written by another deployment (or tenant key) is never unpickled."""
    path = _saved_graph(tmp_path)
    with pytest.raises(IndexIntegrityError):
        load_graph(path, signing_key=b"x" * 32)


def test_load_graph_refuses_a_tampered_blob(tmp_path: Path) -> None:
    path = _saved_graph(tmp_path)
    blob = bytearray(path.read_bytes())
    blob[-1] ^= 0xFF
    path.write_bytes(bytes(blob))
    with pytest.raises(IndexIntegrityError):
        load_graph(path, signing_key=KEY)


def test_load_graph_refuses_an_unsigned_pickle(tmp_path: Path) -> None:
    """Blobs from before signing existed (a bare gzip'd pickle) are refused,
    not executed; the worker falls back to a full rebuild."""
    import gzip
    import pickle

    path = tmp_path / "legacy.graph.pkl.gz"
    path.write_bytes(gzip.compress(pickle.dumps(rx.PyDiGraph())))
    with pytest.raises(IndexIntegrityError):
        load_graph(path, signing_key=KEY)
