"""`find_callers` tool: who (transitively, up to k hops) calls a given
symbol. Thin wrapper over `revu.context.traverse.bounded_expand`, restricted
to `calls` edges in the `callers` direction — the same primitive Stage 6
uses for change-impact traversal, reused here rather than reimplemented.
"""

from __future__ import annotations

import rustworkx as rx
from pydantic import BaseModel, ConfigDict, Field

from revu.agents.tools._common import qualified_name_index
from revu.context.traverse import bounded_expand


class CallerHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    qualified_name: str
    file_path: str
    line_start: int
    line_end: int
    distance: int


class FindCallersResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    found: bool
    qualified_name: str
    callers: list[CallerHit] = Field(default_factory=list)


def find_callers(graph: rx.PyDiGraph, qualified_name: str, *, k: int = 1) -> FindCallersResult:
    """Callers of `qualified_name`, up to `k` hops away (direct callers at
    `k=1`, callers-of-callers included at `k=2`, etc.), nearest first.
    """
    index = qualified_name_index(graph)
    idx = index.get(qualified_name)
    if idx is None:
        return FindCallersResult(found=False, qualified_name=qualified_name)

    hits = bounded_expand(graph, [idx], k=k, edge_kinds=frozenset({"calls"}), direction="callers")

    callers = [
        CallerHit(
            qualified_name=graph[node_idx]["qualified_name"],
            file_path=graph[node_idx]["file_path"],
            line_start=graph[node_idx]["line_start"],
            line_end=graph[node_idx]["line_end"],
            distance=hit.distance,
        )
        for node_idx, hit in hits.items()
        if node_idx != idx  # exclude the seed itself; "who calls me" shouldn't include me
    ]
    callers.sort(key=lambda c: c.distance)

    return FindCallersResult(found=True, qualified_name=qualified_name, callers=callers)
