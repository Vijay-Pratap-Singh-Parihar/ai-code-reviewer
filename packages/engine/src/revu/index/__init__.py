"""Repository indexer: checkout, symbol/import/call extraction, graph assembly, incremental update.

Built in Stage 4, given a full row lifecycle and branch-memory semantics in
Stage 5. Public entry points (no CLI wiring yet — this stage is a plain
importable library; see `IMPLEMENTATION_PLAN.md` Stage 4 for why):

- `build_index(repo_path, commit_sha)` — full index of a real git repo at a commit.
- `build_index_at_path(root)` — full index of a plain directory (no git required).
- `incremental_update(repo_path, old_sha, new_sha, previous_result)` — reparse
  only changed files and re-assemble the graph.
- `save_graph`/`load_graph` — serialise/deserialise the graph blob to disk.
- `save_index_result`/`load_index_result`/`index_result_path` — serialise/
  deserialise the *whole* `IndexResult` (needed to feed `incremental_update`
  across separate process invocations, e.g. one ARQ job per update).
- `is_ancestor`/`resolve_branch_head`/`compute_merge_base`/`resolve_pr_merge_base`
  (`revu.index.vcs`) — force-push/non-fast-forward detection and merge-base
  resolution, per `Product_Architecture_FullStack.md` §2.
"""

from revu.index.graph import IndexResult, build_index, build_index_at_path
from revu.index.incremental import IncrementalUpdateResult, incremental_update
from revu.index.store import (
    index_result_path,
    load_graph,
    load_index_result,
    save_graph,
    save_index_result,
    to_branch_index_fields,
)
from revu.index.vcs import (
    GitRefError,
    MergeBaseInfo,
    NoCommonAncestorError,
    compute_merge_base,
    is_ancestor,
    resolve_branch_head,
    resolve_pr_merge_base,
)

__all__ = [
    "GitRefError",
    "IncrementalUpdateResult",
    "IndexResult",
    "MergeBaseInfo",
    "NoCommonAncestorError",
    "build_index",
    "build_index_at_path",
    "compute_merge_base",
    "incremental_update",
    "index_result_path",
    "is_ancestor",
    "load_graph",
    "load_index_result",
    "resolve_branch_head",
    "resolve_pr_merge_base",
    "save_graph",
    "save_index_result",
    "to_branch_index_fields",
]
