"""Repository indexer: checkout, symbol/import/call extraction, graph assembly, incremental update.

Built in Stage 4. Public entry points (no CLI wiring yet — this stage is a
plain importable library; see `IMPLEMENTATION_PLAN.md` Stage 4 for why):

- `build_index(repo_path, commit_sha)` — full index of a real git repo at a commit.
- `build_index_at_path(root)` — full index of a plain directory (no git required).
- `incremental_update(repo_path, old_sha, new_sha, previous_result)` — reparse
  only changed files and re-assemble the graph.
- `save_graph`/`load_graph` — serialise/deserialise the graph blob to disk.
"""

from revu.index.graph import IndexResult, build_index, build_index_at_path
from revu.index.incremental import IncrementalUpdateResult, incremental_update
from revu.index.store import load_graph, save_graph, to_branch_index_fields

__all__ = [
    "IncrementalUpdateResult",
    "IndexResult",
    "build_index",
    "build_index_at_path",
    "incremental_update",
    "load_graph",
    "save_graph",
    "to_branch_index_fields",
]
