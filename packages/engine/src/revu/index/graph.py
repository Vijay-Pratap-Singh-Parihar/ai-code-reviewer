"""Assemble parsed files into a `rustworkx.PyDiGraph`.

Node kinds: one MODULE node per indexed file, plus one node per
function/class/method `Symbol` found in it. Edge kinds are tagged in each
edge's payload (`{"kind": "imports"}` or `{"kind": "calls"}`) rather than
using a single undifferentiated edge type, because a later stage (context
retrieval ablations) needs to selectively traverse/toggle edge types.

This module also owns the two-pass orchestration import/call resolution
needs: pass 1 parses every file for symbols and raw import edges; pass 2
(which needs the *complete* cross-file symbol table) resolves import
targets and call sites.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import rustworkx as rx

from revu.index import walker
from revu.index.calls import resolve_calls
from revu.index.checkout import worktree_checkout
from revu.index.imports import extract_imports
from revu.index.symbols import discover_source_roots, extract_symbols, module_name_for_file
from revu.index.types import CallEdge, EdgeKind, ImportEdge, Symbol, SymbolKind, UnresolvedRef

logger = logging.getLogger(__name__)


@dataclass
class IndexResult:
    graph: rx.PyDiGraph
    symbols: list[Symbol] = field(default_factory=list)
    import_edges: list[ImportEdge] = field(default_factory=list)
    call_edges: list[CallEdge] = field(default_factory=list)
    unresolved: list[UnresolvedRef] = field(default_factory=list)
    files_indexed: int = 0
    duration_ms: int = 0
    commit_sha: str | None = None

    @property
    def node_count(self) -> int:
        return self.graph.num_nodes()

    @property
    def edge_count(self) -> int:
        return self.graph.num_edges()


@dataclass
class _ParsedFile:
    file_path: str  # posix, relative to the indexed root
    module_name: str
    is_package_init: bool
    source: bytes
    symbols: list[Symbol]
    import_edges: list[ImportEdge]


def _parse_one_file(
    path: Path, root: Path, source_roots: list[Path]
) -> tuple[_ParsedFile | None, list[UnresolvedRef]]:
    """Parse a single file into a `_ParsedFile`. Shared by `_parse_all_files`
    (full build) and `incremental.py` (which parses only changed files,
    without walking the rest of the tree).
    """
    relative_posix = path.relative_to(root).as_posix()
    try:
        source = path.read_bytes()
    except OSError as exc:
        return None, [
            UnresolvedRef(
                kind="file", file_path=relative_posix, line=0, name=relative_posix,
                reason=f"could not read file: {exc}",
            )
        ]

    module_name, is_init = module_name_for_file(path, source_roots)
    try:
        symbols = extract_symbols(source, module_name, relative_posix)
        import_edges, import_unresolved = extract_imports(
            source, module_name=module_name, is_package_init=is_init, file_path=relative_posix
        )
    except Exception as exc:  # a single malformed file must not abort the whole index
        return None, [
            UnresolvedRef(
                kind="file", file_path=relative_posix, line=0, name=relative_posix,
                reason=f"failed to parse: {exc}",
            )
        ]

    parsed = _ParsedFile(
        file_path=relative_posix,
        module_name=module_name,
        is_package_init=is_init,
        source=source,
        symbols=symbols,
        import_edges=import_edges,
    )
    return parsed, import_unresolved


def _parse_all_files(
    root: Path, *, max_file_size: int
) -> tuple[list[_ParsedFile], list[UnresolvedRef]]:
    source_roots = discover_source_roots(root)
    parsed: list[_ParsedFile] = []
    unresolved: list[UnresolvedRef] = []

    for path in walker.iter_python_files(root, max_file_size=max_file_size):
        result, file_unresolved = _parse_one_file(path, root, source_roots)
        unresolved.extend(file_unresolved)
        if result is not None:
            parsed.append(result)

    return parsed, unresolved


def _build_module_scopes(
    import_edges: list[ImportEdge],
    all_symbols: dict[str, Symbol],
    known_modules: set[str],
) -> tuple[dict[str, str], dict[str, str], set[str]]:
    """Split a module's import edges into (local_scope, import_module_scope,
    external_names) for `calls.resolve_calls` — see that function's
    docstring for what each is used for.
    """
    local_scope: dict[str, str] = {}
    import_module_scope: dict[str, str] = {}
    external_names: set[str] = set()

    for edge in import_edges:
        if edge.imported_name == edge.target_module:
            # Plain `import x` / `import x as y`: whole-module import. Kept
            # in `import_module_scope` even when external so an attribute
            # call through it (`os.path.join(...)`) gets the more specific
            # "external module attribute call" reason from `calls.py`
            # rather than falling through to "not found".
            import_module_scope[edge.local_name] = edge.target_module
            if edge.target_module not in known_modules:
                external_names.add(edge.local_name)
            continue

        candidate_symbol = f"{edge.target_module}.{edge.imported_name}"
        if candidate_symbol in all_symbols:
            local_scope[edge.local_name] = candidate_symbol
        elif candidate_symbol in known_modules:
            import_module_scope[edge.local_name] = candidate_symbol
        else:
            # External/stdlib import (e.g. `from fastapi import HTTPException`)
            # or a target this indexer couldn't resolve (already logged by
            # `extract_imports` via the `resolved` flag on the edge itself).
            external_names.add(edge.local_name)

    return local_scope, import_module_scope, external_names


def assemble_graph(
    parsed_files: list[_ParsedFile],
    *,
    extra_call_edges: list[CallEdge] | None = None,
) -> tuple[rx.PyDiGraph, list[Symbol], list[ImportEdge], list[CallEdge], list[UnresolvedRef]]:
    """Build the full graph from already-parsed files. Split out from
    `build_index_at_path` so `incremental.py` can re-assemble the whole
    graph cheaply after only re-parsing the files that changed.

    `extra_call_edges` lets a caller carry forward call edges that were
    already resolved in a previous run and don't need (and, for files with
    no `source` bytes available, *can't* be) re-derived by `resolve_calls`
    here. `incremental.py` uses this for call edges whose caller lives in a
    file that wasn't reparsed this run — see that module for why this
    matters (a real bug found while building Stage 5: without this, every
    incremental update silently dropped every call edge originating in an
    unchanged file, not just ones targeting a renamed/removed symbol).
    An edge is only kept if both its caller and callee still exist as nodes
    in the freshly-assembled graph; one that doesn't is silently dropped,
    matching the existing, documented behaviour for a renamed/removed target.
    """
    known_modules = {pf.module_name for pf in parsed_files}
    all_symbols_list: list[Symbol] = [s for pf in parsed_files for s in pf.symbols]
    all_symbols = {s.qualified_name: s for s in all_symbols_list}

    graph: rx.PyDiGraph = rx.PyDiGraph()
    node_index: dict[str, int] = {}

    def get_or_add_module_node(module_name: str, file_path: str) -> int:
        if module_name in node_index:
            return node_index[module_name]
        idx = graph.add_node(
            {
                "qualified_name": module_name,
                "kind": SymbolKind.MODULE.value,
                "file_path": file_path,
                "line_start": 1,
                "line_end": 1,
            }
        )
        node_index[module_name] = idx
        return idx

    for pf in parsed_files:
        get_or_add_module_node(pf.module_name, pf.file_path)
        for symbol in pf.symbols:
            if symbol.qualified_name in node_index:
                continue  # duplicate qualified name (rare path/naming collision); keep first
            idx = graph.add_node(symbol.model_dump())
            node_index[symbol.qualified_name] = idx

    import_edges: list[ImportEdge] = []
    call_edges: list[CallEdge] = []
    unresolved: list[UnresolvedRef] = []

    for pf in parsed_files:
        local_scope, import_module_scope, external_names = _build_module_scopes(
            pf.import_edges, all_symbols, known_modules
        )
        # Top-level symbols of this module are callable by bare name too.
        for symbol in pf.symbols:
            if symbol.qualified_name.count(".") == pf.module_name.count(".") + 1:
                local_scope.setdefault(symbol.name, symbol.qualified_name)

        for edge in pf.import_edges:
            resolved = edge.target_module in known_modules
            resolved_edge = edge.model_copy(update={"resolved": resolved})
            import_edges.append(resolved_edge)
            if resolved:
                source_idx = node_index[pf.module_name]
                target_idx = get_or_add_module_node(edge.target_module, "")
                graph.add_edge(
                    source_idx, target_idx, {"kind": EdgeKind.IMPORTS.value, "line": edge.line}
                )

        module_call_edges, call_unresolved = resolve_calls(
            pf.source,
            module_name=pf.module_name,
            file_path=pf.file_path,
            module_symbols=pf.symbols,
            local_scope=local_scope,
            import_module_scope=import_module_scope,
            all_symbols=all_symbols,
            known_modules=known_modules,
            external_names=frozenset(external_names),
        )
        call_edges.extend(module_call_edges)
        unresolved.extend(call_unresolved)
        for call_edge in module_call_edges:
            graph.add_edge(
                node_index[call_edge.caller],
                node_index[call_edge.callee],
                {"kind": EdgeKind.CALLS.value, "line": call_edge.line},
            )

    for call_edge in extra_call_edges or []:
        if call_edge.caller in node_index and call_edge.callee in node_index:
            call_edges.append(call_edge)
            graph.add_edge(
                node_index[call_edge.caller],
                node_index[call_edge.callee],
                {"kind": EdgeKind.CALLS.value, "line": call_edge.line},
            )

    return graph, all_symbols_list, import_edges, call_edges, unresolved


def build_index_at_path(
    root: Path, *, commit_sha: str | None = None, max_file_size: int = walker.DEFAULT_MAX_FILE_SIZE
) -> IndexResult:
    """Index every Python file under `root` (a plain directory — no git
    operations). Used directly for benchmarking/indexing an arbitrary
    checkout, and by `build_index` after it materialises a worktree.
    """
    start = time.monotonic()
    parsed_files, parse_unresolved = _parse_all_files(root, max_file_size=max_file_size)
    graph, symbols, import_edges, call_edges, call_unresolved = assemble_graph(parsed_files)
    duration_ms = int((time.monotonic() - start) * 1000)

    return IndexResult(
        graph=graph,
        symbols=symbols,
        import_edges=import_edges,
        call_edges=call_edges,
        unresolved=[*parse_unresolved, *call_unresolved],
        files_indexed=len(parsed_files),
        duration_ms=duration_ms,
        commit_sha=commit_sha,
    )


def build_index(
    repo_path: Path, commit_sha: str, *, max_file_size: int = walker.DEFAULT_MAX_FILE_SIZE
) -> IndexResult:
    """Index `repo_path` (a real git repository) at `commit_sha`, via a
    disposable `git worktree` checkout so the caller's actual working
    directory/branch is never touched.
    """
    with worktree_checkout(repo_path, commit_sha) as worktree_path:
        return build_index_at_path(
            worktree_path, commit_sha=commit_sha, max_file_size=max_file_size
        )
