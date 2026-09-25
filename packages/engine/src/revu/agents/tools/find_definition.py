"""`find_definition` tool: where a symbol (module, class, function, or
method) is actually defined, by qualified name.
"""

from __future__ import annotations

import rustworkx as rx
from pydantic import BaseModel, ConfigDict

from revu.agents.tools._common import qualified_name_index


class DefinitionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    found: bool
    qualified_name: str
    kind: str = ""
    file_path: str = ""
    line_start: int = 0
    line_end: int = 0


def find_definition(graph: rx.PyDiGraph, qualified_name: str) -> DefinitionResult:
    """Look up where `qualified_name` is defined. `found=False` covers both
    "genuinely doesn't exist" and "exists but this indexer's pure name-based
    resolution never linked it up" — the caller can't distinguish those from
    this result alone, same limitation `revu.index` has throughout.
    """
    index = qualified_name_index(graph)
    idx = index.get(qualified_name)
    if idx is None:
        return DefinitionResult(found=False, qualified_name=qualified_name)

    node = graph[idx]
    return DefinitionResult(
        found=True,
        qualified_name=qualified_name,
        kind=node["kind"],
        file_path=node["file_path"],
        line_start=node["line_start"],
        line_end=node["line_end"],
    )
