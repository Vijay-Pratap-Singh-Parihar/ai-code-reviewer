"""`revu.context.impact.blast_radius` on a real index of a small package."""

from pathlib import Path

from revu.context.impact import blast_radius
from revu.index.graph import build_index_at_path

# core.normalize <- service.load <- api.handler (two hops), and
# service.unrelated calls nothing that changes.
CORE = """\
def normalize(value):
    return value.strip().lower()


def untouched():
    return 1
"""
SERVICE = """\
from pkg.core import normalize


def load(name):
    return normalize(name)


def unrelated():
    return 2
"""
API = """\
from pkg.service import load


def handler(request):
    return load(request)
"""


def _graph(tmp_path: Path):  # type: ignore[no-untyped-def]
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text(CORE)
    (pkg / "service.py").write_text(SERVICE)
    (pkg / "api.py").write_text(API)
    return build_index_at_path(tmp_path).graph


DIFF = """\
diff --git a/pkg/core.py b/pkg/core.py
--- a/pkg/core.py
+++ b/pkg/core.py
@@ -1,2 +1,2 @@
 def normalize(value):
-    return value.strip()
+    return value.strip().lower()
"""


def test_callers_up_to_the_depth_are_the_blast_radius(tmp_path: Path) -> None:
    graph = _graph(tmp_path)

    radius = blast_radius(graph, DIFF, depth=2)

    by_name = {n["name"]: n for n in radius["nodes"]}
    assert by_name["pkg.core.normalize"]["distance"] == 0
    assert by_name["pkg.service.load"]["distance"] == 1
    assert by_name["pkg.api.handler"]["distance"] == 2
    assert "pkg.core.untouched" not in by_name and "pkg.service.unrelated" not in by_name
    assert (radius["changed"], radius["impacted"], radius["impacted_files"]) == (1, 2, 2)
    ids = {n["name"]: n["id"] for n in radius["nodes"]}
    edges = radius["edges"]
    assert {"caller": ids["pkg.service.load"], "callee": ids["pkg.core.normalize"]} in edges
    assert {"caller": ids["pkg.api.handler"], "callee": ids["pkg.service.load"]} in edges
    assert radius["truncated"] is False


def test_depth_and_size_limits(tmp_path: Path) -> None:
    graph = _graph(tmp_path)

    one_hop = blast_radius(graph, DIFF, depth=1)
    capped = blast_radius(graph, DIFF, depth=2, max_nodes=2)

    assert {n["name"] for n in one_hop["nodes"]} == {"pkg.core.normalize", "pkg.service.load"}
    # Nearest first; the count still reports everything that was reached.
    assert [n["distance"] for n in capped["nodes"]] == [0, 1]
    assert capped["truncated"] is True and capped["impacted"] == 2


def test_a_diff_outside_the_graph_has_no_radius(tmp_path: Path) -> None:
    graph = _graph(tmp_path)
    readme = "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-a\n+b\n"

    radius = blast_radius(graph, readme)

    assert (radius["changed"], radius["impacted"]) == (0, 0)
    assert (radius["nodes"], radius["edges"]) == ([], [])
