"""Change-impact analysis: diff-to-graph mapping, bounded traversal,
token-budgeted context bundles.

Built in Stage 6. Public entry point:

- `build_context_bundle(diff_text, graph, repo_root, config=...)` - the
  single function later stages (Stage 7's agent layer) call to get a
  `revu.models.ContextBundle` for a diff against a Stage 4 indexed graph.
  Mirrors `revu.agents.diff_only.review_diff`'s "one function, plain
  keyword configuration" shape.

Submodules, in pipeline order:

- `context.diff` - parse a unified diff into per-file hunks.
- `context.mapping` - map hunks onto graph nodes by line overlap.
- `context.traverse` - bounded k-hop expansion from the mapped nodes, with
  every edge kind individually toggleable.
- `context.rank` - score candidates by graph distance + BM25 (plus a
  pluggable, currently-unimplemented embedding-similarity slot).
- `context.budget` - token-budgeted greedy knapsack selection, costed with
  a real tokenizer (`tiktoken`).
- `context.bundle` - wires all of the above into `build_context_bundle`.

See `IMPLEMENTATION_PLAN.md`'s Stage 6 section for why this stage is
validated against hand-verified cases from this repository's own indexed
graph rather than a benchmark recall curve (no ground-truth dataset exists
in this track - see that document's "Explicitly not covered" section).
"""

from revu.context.bundle import RetrievalConfig, build_context_bundle
from revu.context.diff import DiffLine, FileDiff, Hunk, LineKind, parse_diff
from revu.context.mapping import MappedHunk, map_diff_to_nodes
from revu.context.rank import RankWeights
from revu.context.traverse import Direction, TraversalHit, bounded_expand

__all__ = [
    "DiffLine",
    "Direction",
    "FileDiff",
    "Hunk",
    "LineKind",
    "MappedHunk",
    "RankWeights",
    "RetrievalConfig",
    "TraversalHit",
    "bounded_expand",
    "build_context_bundle",
    "map_diff_to_nodes",
    "parse_diff",
]
