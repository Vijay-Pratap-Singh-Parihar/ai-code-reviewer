"""Python import resolution: turn `import`/`from ... import ...` statements
into module dependency edges.

This is explicitly the highest-risk part of the indexer (per the roadmap:
"an 80%-accurate graph that exists beats a perfect graph that does not").
Real Python import resolution requires walking `sys.path`, namespace
packages, `__init__.py` re-exports, conditional imports, etc. This module
does the tractable 90% (absolute imports, `as` aliases, relative imports
with correct dot-counting) and logs everything else to `UnresolvedRef`
rather than guessing or raising:

- Star imports (`from x import *`) — the set of names it introduces can't
  be known without executing `x`, so they're logged and not resolved.
- Relative imports that climb above the repository root.
- Imports inside `try/except ImportError` fallback blocks are still
  extracted (we don't special-case control flow) — if the primary branch
  resolves, the fallback branch's import of the same name is reported as a
  second (likely unresolved) edge, which is accurate to what the source
  actually says even if only one branch executes at runtime.
"""

from __future__ import annotations

import tree_sitter as ts
import tree_sitter_python as tsp

from revu.index.types import ImportEdge, UnresolvedRef

_LANGUAGE = ts.Language(tsp.language())
_PARSER = ts.Parser(_LANGUAGE)


def _iter_nodes_of_type(node: ts.Node, types: set[str]) -> list[ts.Node]:
    found = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in types:
            found.append(current)
        stack.extend(current.children)
    return found


def _text(node: ts.Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def _package_of(module_name: str, is_package_init: bool) -> str:
    if is_package_init:
        return module_name
    if "." not in module_name:
        return ""
    return module_name.rsplit(".", 1)[0]


def _resolve_relative_target(
    relative_import: ts.Node,
    *,
    module_name: str,
    is_package_init: bool,
    file_path: str,
    line: int,
) -> tuple[str | None, UnresolvedRef | None]:
    dot_count = 0
    trailing_dotted_name = None
    for child in relative_import.children:
        if child.type == "import_prefix":
            dot_count = child.text.count(b".") if child.text else 0
        elif child.type == "dotted_name":
            trailing_dotted_name = _text(child)

    base = _package_of(module_name, is_package_init)
    base_parts = base.split(".") if base else []
    # One dot = current package; each extra dot climbs one more level.
    levels_up = dot_count - 1
    if levels_up > len(base_parts):
        return None, UnresolvedRef(
            kind="import",
            file_path=file_path,
            line=line,
            name="." * dot_count + (trailing_dotted_name or ""),
            reason="relative import climbs above the repository/package root",
        )
    target_parts = base_parts[: len(base_parts) - levels_up] if levels_up else base_parts
    if trailing_dotted_name:
        target_parts = [*target_parts, trailing_dotted_name]
    return ".".join(target_parts), None


def extract_imports(
    source: bytes,
    *,
    module_name: str,
    is_package_init: bool,
    file_path: str,
) -> tuple[list[ImportEdge], list[UnresolvedRef]]:
    """Extract import edges from `source`. `known_modules` is intentionally
    not consulted here — `resolved` is set later by `graph.py` once every
    file's module name is known, so import order never affects the result.
    """
    tree = _PARSER.parse(source)
    edges: list[ImportEdge] = []
    unresolved: list[UnresolvedRef] = []

    for node in _iter_nodes_of_type(tree.root_node, {"import_statement"}):
        line = node.start_point[0] + 1
        for child in node.children:
            if child.type == "dotted_name":
                target = _text(child)
                local_name = target.split(".")[0]
                edges.append(
                    ImportEdge(
                        source_module=module_name,
                        target_module=target,
                        imported_name=target,
                        local_name=local_name,
                        file_path=file_path,
                        line=line,
                        resolved=False,
                    )
                )
            elif child.type == "aliased_import":
                dotted = child.child_by_field_name("name")
                alias = child.child_by_field_name("alias")
                target = _text(dotted)
                local_name = _text(alias) or target.split(".")[0]
                edges.append(
                    ImportEdge(
                        source_module=module_name,
                        target_module=target,
                        imported_name=target,
                        local_name=local_name,
                        file_path=file_path,
                        line=line,
                        resolved=False,
                    )
                )

    for node in _iter_nodes_of_type(tree.root_node, {"import_from_statement"}):
        line = node.start_point[0] + 1
        module_node = node.child_by_field_name("module_name")
        if module_node is None:
            continue

        if module_node.type == "relative_import":
            target_module, error = _resolve_relative_target(
                module_node,
                module_name=module_name,
                is_package_init=is_package_init,
                file_path=file_path,
                line=line,
            )
            if error is not None:
                unresolved.append(error)
                continue
        else:
            target_module = _text(module_node)

        if target_module is None:
            continue

        imported_names: list[tuple[str, str]] = []  # (imported_name, local_name)
        found_any_name_node = False
        for child in node.children:
            if child.type == "dotted_name" and child.id != module_node.id:
                found_any_name_node = True
                name = _text(child)
                imported_names.append((name, name))
            elif child.type == "aliased_import":
                found_any_name_node = True
                dotted = child.child_by_field_name("name")
                alias = child.child_by_field_name("alias")
                name = _text(dotted)
                imported_names.append((name, _text(alias) or name))
            elif child.type == "wildcard_import":
                found_any_name_node = True
                unresolved.append(
                    UnresolvedRef(
                        kind="import",
                        file_path=file_path,
                        line=line,
                        name=f"{target_module}.*",
                        reason="star import: introduced names can't be known statically",
                    )
                )

        if not found_any_name_node:
            # `from . import x` where x is a relative_import's own dotted_name
            # (bare package import with no explicit names) — nothing further
            # to record beyond the module edge itself, which we don't have a
            # local name for; skip.
            continue

        for imported_name, local_name in imported_names:
            edges.append(
                ImportEdge(
                    source_module=module_name,
                    target_module=target_module,
                    imported_name=imported_name,
                    local_name=local_name,
                    file_path=file_path,
                    line=line,
                    resolved=False,
                )
            )

    return edges, unresolved
