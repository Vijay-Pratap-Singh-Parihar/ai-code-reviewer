"""Unit tests for `revu.context.bundle.build_context_bundle` against a small,
hand-built graph and real files written to `tmp_path` (so content reading is
exercised for real, without needing a full repository index).

Fixture shape:

`pkg/mod.py`::

    1  import os
    2
    3  X = 1
    4
    5  def foo():
    6      return 1
    7
    8  def bar():
    9      return foo() + 1

`pkg/other.py`::

    1  from pkg.mod import bar
    2
    3
    4  def use_bar():
    5      return bar()

Graph edges: `pkg.other` -imports-> `pkg.mod` (module-level); `bar` -calls->
`foo`; `use_bar` -calls-> `bar`.
"""

from pathlib import Path

import pytest
import rustworkx as rx
from revu.context.bundle import RetrievalConfig, build_context_bundle
from revu.index.types import SymbolKind

MOD_PY = "import os\n\nX = 1\n\ndef foo():\n    return 1\n\ndef bar():\n    return foo() + 1\n"
OTHER_PY = "from pkg.mod import bar\n\n\ndef use_bar():\n    return bar()\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(MOD_PY, encoding="utf-8")
    (tmp_path / "pkg" / "other.py").write_text(OTHER_PY, encoding="utf-8")
    return tmp_path


@pytest.fixture
def graph() -> tuple[rx.PyDiGraph, dict[str, int]]:
    g: rx.PyDiGraph = rx.PyDiGraph()
    nodes: dict[str, int] = {}

    nodes["pkg.mod"] = g.add_node(
        {"qualified_name": "pkg.mod", "kind": SymbolKind.MODULE.value, "file_path": "pkg/mod.py",
         "line_start": 1, "line_end": 1}
    )
    nodes["pkg.mod.foo"] = g.add_node(
        {"qualified_name": "pkg.mod.foo", "kind": SymbolKind.FUNCTION.value,
         "file_path": "pkg/mod.py", "line_start": 5, "line_end": 6}
    )
    nodes["pkg.mod.bar"] = g.add_node(
        {"qualified_name": "pkg.mod.bar", "kind": SymbolKind.FUNCTION.value,
         "file_path": "pkg/mod.py", "line_start": 8, "line_end": 9}
    )
    nodes["pkg.other"] = g.add_node(
        {"qualified_name": "pkg.other", "kind": SymbolKind.MODULE.value,
         "file_path": "pkg/other.py", "line_start": 1, "line_end": 1}
    )
    nodes["pkg.other.use_bar"] = g.add_node(
        {"qualified_name": "pkg.other.use_bar", "kind": SymbolKind.FUNCTION.value,
         "file_path": "pkg/other.py", "line_start": 4, "line_end": 5}
    )

    g.add_edge(nodes["pkg.other"], nodes["pkg.mod"], {"kind": "imports", "line": 1})
    g.add_edge(nodes["pkg.mod.bar"], nodes["pkg.mod.foo"], {"kind": "calls", "line": 9})
    g.add_edge(nodes["pkg.other.use_bar"], nodes["pkg.mod.bar"], {"kind": "calls", "line": 5})

    return g, nodes


def _diff_touching_foo() -> str:
    return (
        "--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -5,2 +5,2 @@\n"
        "-def foo():\n+def foo():  # changed\n     return 1\n"
    )


def _diff_touching_module_level_line() -> str:
    return "--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -3 +3 @@\n-X = 1\n+X = 2\n"


def test_direct_hit_is_the_changed_symbol_with_distance_zero_reason(
    repo: Path, graph: tuple[rx.PyDiGraph, dict[str, int]]
) -> None:
    g, _nodes = graph
    bundle = build_context_bundle(
        _diff_touching_foo(), g, repo, config=RetrievalConfig(k=0, token_budget=10_000)
    )
    foo_items = [i for i in bundle.items if i.file_path == "pkg/mod.py" and i.line_start == 5]
    assert len(foo_items) == 1
    assert "directly changed symbol `pkg.mod.foo`" in foo_items[0].retrieval_reason
    assert "def foo" in foo_items[0].content


def test_one_hop_caller_is_included_with_calls_edge_reason(
    repo: Path, graph: tuple[rx.PyDiGraph, dict[str, int]]
) -> None:
    g, _nodes = graph
    bundle = build_context_bundle(
        _diff_touching_foo(), g, repo, config=RetrievalConfig(k=1, token_budget=10_000)
    )
    bar_items = [i for i in bundle.items if i.line_start == 8]
    assert len(bar_items) == 1
    reason = bar_items[0].retrieval_reason
    assert "1-hop caller via `calls` edge from `pkg.mod.foo`" in reason


def test_two_hop_caller_requires_k_two(
    repo: Path, graph: tuple[rx.PyDiGraph, dict[str, int]]
) -> None:
    g, _nodes = graph
    bundle_k1 = build_context_bundle(
        _diff_touching_foo(), g, repo, config=RetrievalConfig(k=1, token_budget=10_000)
    )
    bundle_k2 = build_context_bundle(
        _diff_touching_foo(), g, repo, config=RetrievalConfig(k=2, token_budget=10_000)
    )
    use_bar_files_k1 = [i for i in bundle_k1.items if i.line_start == 4]
    use_bar_files_k2 = [i for i in bundle_k2.items if i.line_start == 4]
    assert use_bar_files_k1 == []
    assert len(use_bar_files_k2) == 1
    reason = use_bar_files_k2[0].retrieval_reason
    assert "2-hop caller via `calls` edge from `pkg.mod.bar`" in reason


def test_module_level_hunk_with_no_enclosing_symbol_uses_raw_hunk_content(
    repo: Path, graph: tuple[rx.PyDiGraph, dict[str, int]]
) -> None:
    g, _nodes = graph
    config = RetrievalConfig(k=1, token_budget=10_000)
    bundle = build_context_bundle(_diff_touching_module_level_line(), g, repo, config=config)
    # The change is to line 3 ("X = 1"), which is module-level: no function
    # or class encloses it. It must appear with its *real* line number and
    # real content, not the degenerate module node's placeholder (1, 1).
    raw_items = [i for i in bundle.items if i.file_path == "pkg/mod.py" and i.line_start == 3]
    assert len(raw_items) == 1
    assert raw_items[0].content == "X = 1"
    assert "no enclosing function/class found" in raw_items[0].retrieval_reason

    # The module node is still used as a traversal seed even though it isn't
    # used as this hunk's *content* - so the module that imports pkg.mod
    # (pkg.other) is still reachable via the "imports" edge.
    other_items = [i for i in bundle.items if i.file_path == "pkg/other.py" and i.line_start == 1]
    assert len(other_items) == 1
    assert "1-hop caller via `imports` edge from `pkg.mod`" in other_items[0].retrieval_reason


def test_edge_kind_toggle_at_bundle_level(
    repo: Path, graph: tuple[rx.PyDiGraph, dict[str, int]]
) -> None:
    g, _nodes = graph
    calls_only = build_context_bundle(
        _diff_touching_foo(),
        g, repo,
        config=RetrievalConfig(k=1, edge_kinds=frozenset({"calls"}), token_budget=10_000),
    )
    imports_only = build_context_bundle(
        _diff_touching_foo(),
        g, repo,
        config=RetrievalConfig(k=1, edge_kinds=frozenset({"imports"}), token_budget=10_000),
    )
    assert any(i.line_start == 8 for i in calls_only.items)  # bar, via calls
    assert not any(i.line_start == 8 for i in imports_only.items)


def test_bundle_respects_token_budget(
    repo: Path, graph: tuple[rx.PyDiGraph, dict[str, int]]
) -> None:
    g, _nodes = graph
    tiny = build_context_bundle(
        _diff_touching_foo(), g, repo, config=RetrievalConfig(k=2, token_budget=1)
    )
    assert tiny.total_tokens <= 1
    generous = build_context_bundle(
        _diff_touching_foo(), g, repo, config=RetrievalConfig(k=2, token_budget=10_000)
    )
    assert generous.total_tokens <= 10_000
    assert generous.total_tokens >= tiny.total_tokens
    assert len(generous.items) >= len(tiny.items)
