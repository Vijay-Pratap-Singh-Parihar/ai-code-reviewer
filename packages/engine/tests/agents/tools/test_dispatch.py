from pathlib import Path

import rustworkx as rx
from revu.agents.tools import TOOL_SCHEMAS, ToolContext, call_tool


def test_dispatches_read_file(tmp_path: Path, sample_graph: rx.PyDiGraph) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    ctx = ToolContext(tmp_path, sample_graph)

    result = call_tool("read_file", {"file_path": "a.py"}, ctx)
    assert result["found"] is True
    assert result["content"] == "x = 1"


def test_dispatches_graph_query(tmp_path: Path, sample_graph: rx.PyDiGraph) -> None:
    ctx = ToolContext(tmp_path, sample_graph)
    result = call_tool("graph_query", {"qualified_name": "mod_b.bar"}, ctx)
    assert result["found"] is True
    assert result["kind"] == "function"


def test_dispatches_find_definition(tmp_path: Path, sample_graph: rx.PyDiGraph) -> None:
    ctx = ToolContext(tmp_path, sample_graph)
    result = call_tool("find_definition", {"qualified_name": "mod_c.baz"}, ctx)
    assert result["found"] is True


def test_dispatches_find_callers_with_default_k(
    tmp_path: Path, sample_graph: rx.PyDiGraph
) -> None:
    ctx = ToolContext(tmp_path, sample_graph)
    result = call_tool("find_callers", {"qualified_name": "mod_b.bar"}, ctx)
    assert result["found"] is True
    assert [c["qualified_name"] for c in result["callers"]] == ["mod_a.foo"]


def test_unknown_tool_name_returns_error_not_raise(
    tmp_path: Path, sample_graph: rx.PyDiGraph
) -> None:
    ctx = ToolContext(tmp_path, sample_graph)
    result = call_tool("delete_everything", {}, ctx)
    assert "error" in result


def test_missing_required_argument_returns_error_not_raise(
    tmp_path: Path, sample_graph: rx.PyDiGraph
) -> None:
    ctx = ToolContext(tmp_path, sample_graph)
    result = call_tool("graph_query", {}, ctx)
    assert "error" in result


def test_wrong_argument_type_returns_error_not_raise(
    tmp_path: Path, sample_graph: rx.PyDiGraph
) -> None:
    ctx = ToolContext(tmp_path, sample_graph)
    result = call_tool("find_callers", {"qualified_name": "mod_b.bar", "k": "not-a-number"}, ctx)
    assert "error" in result


def test_every_tool_schema_is_well_formed() -> None:
    names = [schema["function"]["name"] for schema in TOOL_SCHEMAS]
    assert names == ["read_file", "graph_query", "find_definition", "find_callers"]
    for schema in TOOL_SCHEMAS:
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["description"]
        assert fn["parameters"]["type"] == "object"
        assert isinstance(fn["parameters"]["required"], list)
        for required_field in fn["parameters"]["required"]:
            assert required_field in fn["parameters"]["properties"]
