"""Shared data shapes for the indexer.

Kept separate from `graph.py` so `symbols.py`/`imports.py`/`calls.py` can
depend on the plain data shapes without importing the (heavier) graph
assembly module, and so `store.py` can serialise them without a circular
import back into `graph.py`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class SymbolKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


class EdgeKind(StrEnum):
    IMPORTS = "imports"
    CALLS = "calls"


class Symbol(BaseModel):
    """One node in the graph: a module, class, function, or method.

    `qualified_name` is the join key used everywhere else in the indexer
    (import resolution, call resolution, graph node identity) — see
    `symbols.module_name_for_file` for how it's derived from a file path.
    """

    model_config = ConfigDict(frozen=True)

    qualified_name: str
    name: str
    kind: SymbolKind
    file_path: str
    line_start: int
    line_end: int


class ImportEdge(BaseModel):
    """A module-level dependency edge: `source_module` imports something
    named `imported_name` (bound locally as `local_name`) from
    `target_module`. `resolved` is True only when `target_module` is itself
    one of the modules this indexer discovered in the same run — an import
    of an external/stdlib package is a perfectly normal, expected edge, just
    one this indexer can't traverse any further.
    """

    model_config = ConfigDict(frozen=True)

    source_module: str
    target_module: str
    imported_name: str
    local_name: str
    file_path: str
    line: int
    resolved: bool


class CallEdge(BaseModel):
    """A name-resolved call site: `caller` (a function/method qualified
    name) calls `callee` (a function/class/method qualified name). Pure
    name-based resolution, no type inference — see `calls.py` module
    docstring for exactly what that does and doesn't cover.
    """

    model_config = ConfigDict(frozen=True)

    caller: str
    callee: str
    file_path: str
    line: int


class UnresolvedRef(BaseModel):
    """Something the indexer looked at but could not resolve. Logged, not
    raised — per the roadmap, an 80%-accurate graph that exists beats a
    perfect graph that does not.
    """

    model_config = ConfigDict(frozen=True)

    kind: str  # "import" | "call" | "file"
    file_path: str
    line: int
    name: str
    reason: str
