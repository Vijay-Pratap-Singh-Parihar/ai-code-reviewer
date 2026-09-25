import rustworkx as rx
from revu.agents.tools.find_definition import find_definition


def test_unknown_symbol(sample_graph: rx.PyDiGraph) -> None:
    result = find_definition(sample_graph, "nope")
    assert result.found is False


def test_finds_a_function(sample_graph: rx.PyDiGraph) -> None:
    result = find_definition(sample_graph, "mod_c.baz")
    assert result.found is True
    assert result.kind == "function"
    assert result.file_path == "mod_c.py"
    assert (result.line_start, result.line_end) == (1, 2)


def test_finds_a_module(sample_graph: rx.PyDiGraph) -> None:
    result = find_definition(sample_graph, "mod_a")
    assert result.found is True
    assert result.kind == "module"
