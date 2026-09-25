"""`graph_query` tool: look up a symbol's node metadata and every edge
directly touching it (one hop each direction) by qualified name.
"""

from __future__ import annotations

import rustworkx as rx
from pydantic import BaseModel, ConfigDict, Field

from revu.agents.tools._common import qualified_name_index


class EdgeInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    qualified_name: str
    file_path: str
    line_start: int
    line_end: int
    at_line: int


class GraphQueryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    found: bool
    qualified_name: str
    kind: str = ""
    file_path: str = ""
    line_start: int = 0
    line_end: int = 0
    outgoing: list[EdgeInfo] = Field(default_factory=list)
    incoming: list[EdgeInfo] = Field(default_factory=list)


def graph_query(graph: rx.PyDiGraph, qualified_name: str) -> GraphQueryResult:
    """Look up `qualified_name`'s node info plus every edge directly
    touching it - what it imports/calls (`outgoing`), and what
    imports/calls it (`incoming`). Uses `out_edges`/`in_edges` rather than
    `get_edge_data`/`successor_indices`, which silently collapse multiple
    edges between the same node pair (e.g. a function calling the same
    callee twice, at different lines) down to one.
    """
    index = qualified_name_index(graph)
    idx = index.get(qualified_name)
    if idx is None:
        return GraphQueryResult(found=False, qualified_name=qualified_name)

    node = graph[idx]

    def _edge_info(other_idx: int, payload: dict[str, object]) -> EdgeInfo:
        other = graph[other_idx]
        at_line = payload["line"]
        assert isinstance(at_line, int)
        return EdgeInfo(
            kind=str(payload["kind"]),
            qualified_name=other["qualified_name"],
            file_path=other["file_path"],
            line_start=other["line_start"],
            line_end=other["line_end"],
            at_line=at_line,
        )

    outgoing = [_edge_info(target, payload) for _src, target, payload in graph.out_edges(idx)]
    incoming = [_edge_info(source, payload) for source, _tgt, payload in graph.in_edges(idx)]

    return GraphQueryResult(
        found=True,
        qualified_name=qualified_name,
        kind=node["kind"],
        file_path=node["file_path"],
        line_start=node["line_start"],
        line_end=node["line_end"],
        outgoing=outgoing,
        incoming=incoming,
    )
