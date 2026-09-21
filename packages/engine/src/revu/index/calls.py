"""Name-based call-site resolution.

Deliberately does **not** attempt type inference, per the roadmap: we never
try to figure out what class a local variable is an instance of. The only
"typed" resolution performed is `self.method()` inside a method — and that
isn't type inference, it's a static fact of Python syntax (the enclosing
class *is* self's type by definition of how methods are dispatched).

What resolves:
- `bare_name()` — a same-module top-level function/class, or a name pulled
  into scope via `from x import bare_name`.
- `self.method()` inside a method — resolved against the enclosing class.
- `module_alias.func()` where `module_alias` is a locally imported module
  that this indexing run also discovered (`import revu.index.symbols as s`
  then `s.extract_symbols(...)`).

What does not resolve (logged as `UnresolvedRef`, not guessed):
- Calls through a local variable (`reviewer = Reviewer(); reviewer.run()`)
  — would require type inference to know `reviewer`'s type.
- Attribute chains deeper than one hop (`a.b.c()`).
- Calls to names not found in module scope or import scope (typos,
  builtins, dynamically injected names, star-imported names).
- Calls made at module level (outside any function/method body) — these
  have no "caller" symbol to attach the edge to, so they're skipped
  entirely rather than invented a synthetic module-level caller node.
"""

from __future__ import annotations

import builtins
from collections.abc import Mapping

import tree_sitter as ts
import tree_sitter_python as tsp

from revu.index.types import CallEdge, Symbol, SymbolKind, UnresolvedRef

_LANGUAGE = ts.Language(tsp.language())
_PARSER = ts.Parser(_LANGUAGE)
_BUILTIN_NAMES = frozenset(vars(builtins))


def _text(node: ts.Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def _iter_call_nodes(node: ts.Node) -> list[ts.Node]:
    found = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "call":
            found.append(current)
        stack.extend(current.children)
    return found


def _enclosing_symbol(
    line: int, module_symbols: list[Symbol]
) -> Symbol | None:
    """The innermost function/method symbol (by tightest line range) whose
    body contains `line`. Classes are excluded — a call textually inside a
    class body but outside any method (rare: default-argument expressions,
    class-level constants) is not attributed to the class itself.
    """
    best: Symbol | None = None
    for symbol in module_symbols:
        if symbol.kind not in (SymbolKind.FUNCTION, SymbolKind.METHOD):
            continue
        in_range = symbol.line_start <= line <= symbol.line_end
        tighter = best is None or (symbol.line_end - symbol.line_start) < (
            best.line_end - best.line_start
        )
        if in_range and tighter:
            best = symbol
    return best


def resolve_calls(
    source: bytes,
    *,
    module_name: str,
    file_path: str,
    module_symbols: list[Symbol],
    local_scope: Mapping[str, str],
    import_module_scope: Mapping[str, str],
    all_symbols: Mapping[str, Symbol],
    known_modules: set[str],
    external_names: frozenset[str] = frozenset(),
) -> tuple[list[CallEdge], list[UnresolvedRef]]:
    """Resolve call sites in one file to qualified callee names.

    - `module_symbols`: symbols defined in this file (for enclosing-scope
      lookup and for the local top-level name -> qualified-name map).
    - `local_scope`: local name -> qualified name, for names pulled in via
      `from x import name` and top-level definitions in this module.
    - `import_module_scope`: local alias -> target module name, for plain
      `import x` / `import x as y` (whole-module imports).
    - `all_symbols`: qualified name -> Symbol, across the *entire* indexed
      repository (needed to confirm a resolved target actually exists).
    - `known_modules`: every module name discovered in this indexing run
      (used to distinguish "internal module we just can't see the symbol
      in" from "external/stdlib import", so only genuine gaps get logged).
    - `external_names`: local names imported from a module this indexing
      run did not discover (e.g. `from fastapi import HTTPException`) —
      used only to give these a more accurate `UnresolvedRef.reason` than
      "not found", since they aren't a resolution failure, just outside
      this indexer's Python-only, single-repo scope.
    """
    tree = _PARSER.parse(source)
    edges: list[CallEdge] = []
    unresolved: list[UnresolvedRef] = []

    for call_node in _iter_call_nodes(tree.root_node):
        line = call_node.start_point[0] + 1
        caller = _enclosing_symbol(line, module_symbols)
        if caller is None:
            continue  # module-level call site; no caller symbol to attach to

        function_node = call_node.child_by_field_name("function")
        if function_node is None:
            continue

        callee_qualified: str | None = None
        callee_name_for_log = _text(function_node)
        reason = ""

        if function_node.type == "identifier":
            name = _text(function_node)
            if name in local_scope:
                callee_qualified = local_scope[name]
            elif name in external_names:
                reason = "external import (module not indexed)"
            elif name in _BUILTIN_NAMES:
                reason = "python builtin"
            else:
                reason = "name not found in module scope or imports"

        elif function_node.type == "attribute":
            obj_node = function_node.child_by_field_name("object")
            attr_node = function_node.child_by_field_name("attribute")
            attr_name = _text(attr_node)

            if obj_node is not None and obj_node.type == "identifier":
                obj_name = _text(obj_node)
                if obj_name == "self" and caller.kind == SymbolKind.METHOD:
                    class_qualified = caller.qualified_name.rsplit(".", 1)[0]
                    callee_qualified = f"{class_qualified}.{attr_name}"
                elif obj_name in import_module_scope:
                    target_module = import_module_scope[obj_name]
                    candidate = f"{target_module}.{attr_name}"
                    if target_module in known_modules:
                        callee_qualified = candidate
                    else:
                        reason = "external module attribute call (module not indexed)"
                elif (
                    obj_name in local_scope
                    and local_scope[obj_name] in all_symbols
                    and all_symbols[local_scope[obj_name]].kind == SymbolKind.CLASS
                ):
                    # `ClassName.static_or_class_method()` — a static fact of
                    # the source (the base is a class name, not a variable),
                    # not type inference.
                    callee_qualified = f"{local_scope[obj_name]}.{attr_name}"
                else:
                    reason = "call through a local variable requires type inference"
            else:
                reason = "attribute chain deeper than one hop is not resolved"
        else:
            reason = f"unsupported call target node type: {function_node.type}"

        if callee_qualified is not None and callee_qualified in all_symbols:
            edges.append(
                CallEdge(
                    caller=caller.qualified_name,
                    callee=callee_qualified,
                    file_path=file_path,
                    line=line,
                )
            )
        else:
            if callee_qualified is not None:
                reason = "resolved a target module/name but no matching symbol was indexed"
            unresolved.append(
                UnresolvedRef(
                    kind="call",
                    file_path=file_path,
                    line=line,
                    name=callee_name_for_log,
                    reason=reason or "unresolved call target",
                )
            )

    return edges, unresolved
