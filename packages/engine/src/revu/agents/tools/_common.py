"""Shared helpers for tool implementations.

Graph nodes carry no reverse pointer back to their own index (see
`revu.index.graph`'s node payload shape), so every tool that looks a symbol
up by name needs this index. Built fresh per call rather than cached, since
tools are meant to be cheap, stateless, structured-data functions — not
holders of their own mutable state across an agent's tool-calling loop.
"""

from __future__ import annotations

import rustworkx as rx


def qualified_name_index(graph: rx.PyDiGraph) -> dict[str, int]:
    """`qualified_name -> node index` for every node in the graph."""
    return {graph[idx]["qualified_name"]: idx for idx in graph.node_indices()}
