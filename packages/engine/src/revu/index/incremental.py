"""Incremental index update: reparse only the files that changed between two
commits and re-assemble the graph from the merged symbol/edge lists.

**Design choice:** rather than surgically patching the existing
`rustworkx.PyDiGraph` object in place (add/remove specific nodes/edges),
this re-runs `graph.assemble_graph` over `unchanged symbols/edges (kept) +
changed files (re-parsed)`. Graph assembly itself is O(nodes + edges) and
fast (it's pure Python dict/list bookkeeping, no I/O, no parsing) — the
expensive part of a full rebuild is re-parsing and re-walking every file on
disk, and that's exactly what's skipped here. This is simpler and harder to
get subtly wrong than incremental graph surgery, at effectively no cost to
the <5s incremental-update budget.

**Known limitation, documented rather than silently accepted:** if a
changed file renames or removes a symbol, call edges *from unchanged files*
into that old symbol are not corrected — they simply vanish (the target
node no longer exists), so the caller-side file would need to be
re-processed too to notice its call is now unresolved. A full rebuild is
the only way to guarantee every edge reflects the latest state everywhere;
incremental update trades that guarantee for speed, per the roadmap's
"good enough" philosophy for this stage.

**Real bug found and fixed in Stage 5:** unchanged files are reconstructed
as `_ParsedFile`s with `source=b""` (their symbols/imports are reused
verbatim; there's no need to re-read the file from disk). But
`assemble_graph` used to call `resolve_calls(pf.source, ...)` on *every*
`_ParsedFile` it was given, including these — parsing an empty byte string
finds zero call sites, so every call edge whose caller lived in an unchanged
file silently disappeared on every incremental update, not just ones
targeting a renamed/removed symbol as documented above. `assemble_graph` now
takes an `extra_call_edges` parameter for exactly this: call edges from
`previous_result` whose `file_path` (the caller's file, not the callee's)
isn't in the changed set are carried forward and re-attached to the graph
verbatim (dropped only if their caller or callee node no longer exists,
which is the documented rename/removal case, now correctly the *only* way
an edge disappears here).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from git import Repo

from revu.index import walker
from revu.index.checkout import worktree_checkout
from revu.index.graph import IndexResult, _parse_one_file, _ParsedFile, assemble_graph
from revu.index.symbols import discover_source_roots, module_name_for_file
from revu.index.types import UnresolvedRef


@dataclass
class IncrementalUpdateResult:
    index: IndexResult
    files_reparsed: int
    files_removed: int


def changed_python_files(repo_path: Path, old_sha: str, new_sha: str) -> list[str]:
    """`git diff --name-only` between two commits, filtered to `.py` files."""
    repo = Repo(repo_path)
    output = repo.git.diff("--name-only", old_sha, new_sha)
    return [line for line in output.splitlines() if line.endswith(".py")]


def incremental_update(
    repo_path: Path,
    old_sha: str,
    new_sha: str,
    previous_result: IndexResult,
    *,
    max_file_size: int = walker.DEFAULT_MAX_FILE_SIZE,
) -> IncrementalUpdateResult:
    """Reparse only the files that changed between `old_sha` and `new_sha`,
    reusing `previous_result`'s data for everything else.
    """
    start = time.monotonic()
    changed_paths = set(changed_python_files(repo_path, old_sha, new_sha))

    with worktree_checkout(repo_path, new_sha) as worktree_path:
        source_roots = discover_source_roots(worktree_path)
        still_present: set[str] = set()

        reparsed: list[_ParsedFile] = []
        reparse_unresolved: list[UnresolvedRef] = []
        for relative_path in changed_paths:
            full_path = worktree_path / relative_path
            if not full_path.is_file():
                continue  # deleted in new_sha
            if full_path.stat().st_size > max_file_size:
                continue
            still_present.add(relative_path)
            parsed_file, file_unresolved = _parse_one_file(full_path, worktree_path, source_roots)
            reparse_unresolved.extend(file_unresolved)
            if parsed_file is not None:
                reparsed.append(parsed_file)

        kept_symbols = [s for s in previous_result.symbols if s.file_path not in changed_paths]
        kept_import_edges = [
            e for e in previous_result.import_edges if e.file_path not in changed_paths
        ]
        kept_unresolved = [
            u for u in previous_result.unresolved if u.file_path not in changed_paths
        ]
        # `CallEdge.file_path` is the *caller's* file — a call edge survives
        # unless the file it's made from was reparsed this run (see the
        # module docstring's "real bug found and fixed in Stage 5" note).
        kept_call_edges = [
            c for c in previous_result.call_edges if c.file_path not in changed_paths
        ]

        # Rebuild the `_ParsedFile` list for unchanged files from the kept
        # symbols/imports (we don't keep raw source around for unchanged
        # files, and don't need to — calls for unchanged files are not
        # recomputed, only the new files' calls are, per the limitation
        # documented above).
        unchanged_by_module: dict[str, _ParsedFile] = {}
        for symbol in kept_symbols:
            unchanged_by_module.setdefault(
                symbol.file_path,
                _ParsedFile(
                    file_path=symbol.file_path,
                    module_name="",
                    is_package_init=False,
                    source=b"",
                    symbols=[],
                    import_edges=[],
                ),
            ).symbols.append(symbol)
        for edge in kept_import_edges:
            pf = unchanged_by_module.setdefault(
                edge.file_path,
                _ParsedFile(
                    file_path=edge.file_path,
                    module_name=edge.source_module,
                    is_package_init=False,
                    source=b"",
                    symbols=[],
                    import_edges=[],
                ),
            )
            pf.module_name = edge.source_module
            pf.import_edges.append(edge)
        for pf in unchanged_by_module.values():
            if not pf.module_name and pf.symbols:
                # Derive module name from the first symbol if this file had
                # no import edges to infer it from.
                path_obj = worktree_path / pf.file_path
                pf.module_name, pf.is_package_init = module_name_for_file(path_obj, source_roots)

        merged_parsed = [*unchanged_by_module.values(), *reparsed]
        graph, symbols, import_edges, call_edges, call_unresolved = assemble_graph(
            merged_parsed, extra_call_edges=kept_call_edges
        )

        duration_ms = int((time.monotonic() - start) * 1000)
        result = IndexResult(
            graph=graph,
            symbols=symbols,
            import_edges=import_edges,
            call_edges=call_edges,
            unresolved=[*kept_unresolved, *reparse_unresolved, *call_unresolved],
            files_indexed=len(merged_parsed),
            duration_ms=duration_ms,
            commit_sha=new_sha,
        )
        return IncrementalUpdateResult(
            index=result,
            files_reparsed=len(reparsed),
            files_removed=len(changed_paths - still_present),
        )
