"""Tree-sitter symbol extraction: functions, classes, and methods with
qualified names and line ranges.

Qualified names are built to match real Python import paths wherever
possible (see `discover_source_roots`/`module_name_for_file`), because
`imports.py` and `calls.py` both key off them to resolve cross-file
references. For a repo that doesn't follow a recognisable `src/` or
flat-package layout, the fallback (path relative to the repo root, dots for
slashes) still gives every symbol a stable, unique qualified name — it just
won't necessarily match what another file's `import` statement spells.
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter as ts
import tree_sitter_python as tsp

from revu.index.types import Symbol, SymbolKind

_LANGUAGE = ts.Language(tsp.language())
_PARSER = ts.Parser(_LANGUAGE)
_DEF_QUERY = ts.Query(
    _LANGUAGE,
    """
    (function_definition name: (identifier) @name) @def
    (class_definition name: (identifier) @name) @def
    """,
)


def discover_source_roots(repo_root: Path) -> list[Path]:
    """Find the directories Python import statements would actually resolve
    against, by locating packages (a directory containing `__init__.py`)
    either directly under a project directory (flat layout) or under its
    `src/` subdirectory (src layout) — the two conventions covering the
    overwhelming majority of real Python projects, including this one.

    Returns roots ordered longest-path-first, so `module_name_for_file` can
    pick the most specific (deepest) root that contains a given file.
    Always includes `repo_root` itself as a last-resort fallback.
    """
    roots: set[Path] = set()
    for init_file in repo_root.rglob("__init__.py"):
        if any(part in {".git", "node_modules", ".venv", "venv"} for part in init_file.parts):
            continue
        package_dir = init_file.parent
        # Walk up while parents also contain __init__.py — the source root
        # is the first ancestor that is *not* itself a package (i.e. the
        # directory a top-level `import <package>` would resolve against).
        candidate = package_dir
        while (candidate.parent / "__init__.py").exists():
            candidate = candidate.parent
        roots.add(candidate.parent)

    roots.add(repo_root)
    return sorted(roots, key=lambda p: len(p.parts), reverse=True)


def module_name_for_file(file_path: Path, source_roots: list[Path]) -> tuple[str, bool]:
    """Return `(module_name, is_package_init)` for `file_path`.

    `is_package_init` matters to `imports.py`'s relative-import resolution:
    a `from . import x` inside a package's `__init__.py` means something
    different (siblings within that same package) than the same statement
    inside a regular submodule (siblings within its parent package).
    """
    resolved = file_path.resolve()
    for root in source_roots:
        try:
            relative = resolved.relative_to(root.resolve())
        except ValueError:
            continue
        parts = list(relative.parts)
        is_init = parts[-1] == "__init__.py"
        if is_init:
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1].removesuffix(".py")
        if not parts:
            # __init__.py directly at a source root: name it after the root dir.
            return root.name, True
        return ".".join(parts), is_init
    # Should not happen since discover_source_roots always includes the repo
    # root, but keep a safe fallback rather than raising.
    return file_path.stem, file_path.name == "__init__.py"


def _enclosing_definitions(node: ts.Node) -> list[ts.Node]:
    """Ancestors of `node` that are themselves function/class definitions,
    outermost first."""
    ancestors = []
    current = node.parent
    while current is not None:
        if current.type in ("function_definition", "class_definition"):
            ancestors.append(current)
        current = current.parent
    return list(reversed(ancestors))


def _node_name(node: ts.Node) -> str:
    name_node = node.child_by_field_name("name")
    assert name_node is not None and name_node.text is not None
    return name_node.text.decode("utf-8")


def extract_symbols(source: bytes, module_name: str, file_path: str) -> list[Symbol]:
    """Extract every function/class/method definition in `source`.

    `module_name` becomes the qualified-name prefix (module itself is not
    emitted as a `Symbol` here — see `graph.py`, which adds one module node
    per file separately, since imports connect modules, not symbol nodes).
    """
    tree = _PARSER.parse(source)
    cursor = ts.QueryCursor(_DEF_QUERY)
    matches = cursor.matches(tree.root_node)

    symbols: list[Symbol] = []
    seen_nodes: set[int] = set()
    for _pattern_index, captures in matches:
        def_nodes = captures.get("def", [])
        for def_node in def_nodes:
            if def_node.id in seen_nodes:
                continue
            seen_nodes.add(def_node.id)

            ancestors = _enclosing_definitions(def_node)
            name_chain = [_node_name(a) for a in ancestors] + [_node_name(def_node)]
            qualified_name = ".".join([module_name, *name_chain])

            if def_node.type == "class_definition":
                kind = SymbolKind.CLASS
            elif ancestors and ancestors[-1].type == "class_definition":
                kind = SymbolKind.METHOD
            else:
                kind = SymbolKind.FUNCTION

            symbols.append(
                Symbol(
                    qualified_name=qualified_name,
                    name=_node_name(def_node),
                    kind=kind,
                    file_path=file_path,
                    line_start=def_node.start_point[0] + 1,
                    line_end=def_node.end_point[0] + 1,
                )
            )
    return symbols
