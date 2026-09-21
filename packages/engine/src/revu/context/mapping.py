"""Map parsed diff hunks onto nodes of a Stage 4 indexed graph.

A hunk maps to every symbol node (function/class/method) in the same file
whose `[line_start, line_end]` overlaps the hunk's touched new-file line
range - a hunk can span more than one symbol (e.g. a change that spans the
end of one function and the start of the next), and both are returned. If no
symbol overlaps (the hunk touches module-level code - imports, top-level
constants, or a blank/comment-only region), the file's MODULE node is
returned instead, when the file is indexed at all.
"""

from __future__ import annotations

from dataclasses import dataclass

import rustworkx as rx

from revu.context.diff import FileDiff, Hunk
from revu.index.types import SymbolKind


@dataclass(frozen=True)
class MappedHunk:
    """One hunk together with the graph nodes it was mapped to."""

    file_path: str
    line_start: int
    line_end: int
    node_indices: tuple[int, ...]
    matched_module_only: bool


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def build_file_index(graph: rx.PyDiGraph) -> dict[str, list[int]]:
    """`file_path -> [node indices]` for every node the graph has a
    `file_path` for (every node - module and symbol alike - carries one).
    """
    index: dict[str, list[int]] = {}
    for node_index in graph.node_indices():
        payload = graph[node_index]
        file_path = payload.get("file_path")
        if not file_path:
            continue
        index.setdefault(file_path, []).append(node_index)
    return index


def map_range_to_nodes(
    graph: rx.PyDiGraph,
    file_index: dict[str, list[int]],
    *,
    file_path: str,
    line_start: int,
    line_end: int,
) -> tuple[tuple[int, ...], bool]:
    """Map one `(file_path, line_start, line_end)` range to node indices.

    Returns `(node_indices, matched_module_only)`. `node_indices` is empty
    when the file isn't present in the indexed graph at all (e.g. a change
    to a non-Python file, or a file the indexer skipped).
    """
    candidates = file_index.get(file_path, [])
    if not candidates:
        return (), False

    overlapping_symbols = [
        idx
        for idx in candidates
        if graph[idx]["kind"] != SymbolKind.MODULE.value
        and _ranges_overlap(graph[idx]["line_start"], graph[idx]["line_end"], line_start, line_end)
    ]
    if overlapping_symbols:
        return tuple(overlapping_symbols), False

    module_nodes = [idx for idx in candidates if graph[idx]["kind"] == SymbolKind.MODULE.value]
    return tuple(module_nodes), bool(module_nodes)


def map_hunk_to_nodes(
    graph: rx.PyDiGraph, file_index: dict[str, list[int]], *, file_path: str, hunk: Hunk
) -> MappedHunk:
    line_start, line_end = hunk.new_line_range()
    node_indices, matched_module_only = map_range_to_nodes(
        graph, file_index, file_path=file_path, line_start=line_start, line_end=line_end
    )
    return MappedHunk(
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        node_indices=node_indices,
        matched_module_only=matched_module_only,
    )


def map_diff_to_nodes(graph: rx.PyDiGraph, file_diffs: list[FileDiff]) -> list[MappedHunk]:
    """Map every hunk of every file in a parsed diff onto graph nodes."""
    file_index = build_file_index(graph)
    mapped: list[MappedHunk] = []
    for file_diff in file_diffs:
        for hunk in file_diff.hunks:
            mapped.append(
                map_hunk_to_nodes(graph, file_index, file_path=file_diff.file_path, hunk=hunk)
            )
    return mapped
