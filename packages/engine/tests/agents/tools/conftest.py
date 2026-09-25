import pytest
import rustworkx as rx


@pytest.fixture
def sample_graph() -> rx.PyDiGraph:
    """A small synthetic graph matching `revu.index.graph`'s real node/edge
    payload shape: three modules, one function each, `mod_a.foo` calls
    `mod_b.bar` which calls `mod_c.baz`, plus a plain module-level import
    edge from `mod_a` to `mod_b`.
    """
    g: rx.PyDiGraph = rx.PyDiGraph()

    def add_module(name: str, file_path: str) -> int:
        return g.add_node(
            {"qualified_name": name, "kind": "module", "file_path": file_path,
             "line_start": 1, "line_end": 1}
        )

    def add_function(name: str, file_path: str, line_start: int, line_end: int) -> int:
        return g.add_node(
            {"qualified_name": name, "kind": "function", "file_path": file_path,
             "line_start": line_start, "line_end": line_end}
        )

    mod_a = add_module("mod_a", "mod_a.py")
    mod_b = add_module("mod_b", "mod_b.py")
    add_module("mod_c", "mod_c.py")

    foo = add_function("mod_a.foo", "mod_a.py", 3, 5)
    bar = add_function("mod_b.bar", "mod_b.py", 10, 15)
    baz = add_function("mod_c.baz", "mod_c.py", 1, 2)

    g.add_edge(mod_a, mod_b, {"kind": "imports", "line": 1})
    g.add_edge(foo, bar, {"kind": "calls", "line": 4})
    g.add_edge(bar, baz, {"kind": "calls", "line": 12})

    return g
