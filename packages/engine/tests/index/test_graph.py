from pathlib import Path

from revu.index.graph import build_index_at_path
from revu.index.types import EdgeKind, SymbolKind


def _write_project(root: Path) -> None:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "utils.py").write_text(
        "def helper():\n"
        "    return 1\n"
    )
    (pkg / "main.py").write_text(
        "from pkg.utils import helper\n"
        "\n"
        "class Service:\n"
        "    def run(self):\n"
        "        return helper()\n"
        "\n"
        "    def call_self(self):\n"
        "        self.run()\n"
    )


def test_build_index_at_path_produces_module_and_symbol_nodes(tmp_path: Path) -> None:
    _write_project(tmp_path)
    result = build_index_at_path(tmp_path)

    assert result.files_indexed == 3
    qualified_names = {s.qualified_name for s in result.symbols}
    assert "pkg.utils.helper" in qualified_names
    assert "pkg.main.Service" in qualified_names
    assert "pkg.main.Service.run" in qualified_names
    assert "pkg.main.Service.call_self" in qualified_names

    node_payloads = [result.graph[i] for i in result.graph.node_indices()]
    module_nodes = [n for n in node_payloads if n["kind"] == SymbolKind.MODULE.value]
    assert {n["qualified_name"] for n in module_nodes} == {"pkg", "pkg.utils", "pkg.main"}


def test_build_index_at_path_resolves_import_and_call_edges(tmp_path: Path) -> None:
    _write_project(tmp_path)
    result = build_index_at_path(tmp_path)

    import_targets = {(e.source_module, e.target_module) for e in result.import_edges if e.resolved}
    assert ("pkg.main", "pkg.utils") in import_targets

    calls = {(e.caller, e.callee) for e in result.call_edges}
    assert ("pkg.main.Service.run", "pkg.utils.helper") in calls
    assert ("pkg.main.Service.call_self", "pkg.main.Service.run") in calls


def test_graph_edges_are_tagged_with_their_kind(tmp_path: Path) -> None:
    _write_project(tmp_path)
    result = build_index_at_path(tmp_path)

    edge_kinds = {result.graph.get_edge_data(u, v)["kind"] for u, v in result.graph.edge_list()}
    assert edge_kinds == {EdgeKind.IMPORTS.value, EdgeKind.CALLS.value}


def test_malformed_file_is_logged_and_does_not_abort_indexing(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_bytes(b"\xff\xfe not even close to valid utf-8 \x00\x01")
    (tmp_path / "fine.py").write_text("def ok():\n    pass\n")

    # A file with invalid bytes still parses under tree-sitter (it treats
    # source as a byte stream), so this mainly proves indexing tolerates
    # unusual content without raising rather than requiring valid UTF-8.
    result = build_index_at_path(tmp_path)
    assert result.files_indexed == 2


def test_empty_directory_produces_an_empty_but_valid_index(tmp_path: Path) -> None:
    result = build_index_at_path(tmp_path)
    assert result.files_indexed == 0
    assert result.node_count == 0
    assert result.edge_count == 0
