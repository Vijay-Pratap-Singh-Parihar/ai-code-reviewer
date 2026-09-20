"""Persist an indexed graph as a serialised blob, and map an `IndexResult`
onto the `branch_index` table's column shape.

**Design decision (Stage 4):** `Product_Architecture_FullStack.md` §5 lists
two options for graph storage — "Postgres tables, or a serialised
`rustworkx` blob per snapshot" — and says to benchmark both, noting tables
would allow SQL queries over structure. Stage 4 has no feature that needs
to `SELECT` over graph structure yet (that's Stage 6+, context retrieval),
so the blob is the right call now: it's a straight `pickle`+`gzip` of the
`PyDiGraph` (rustworkx graphs pickle natively), written to a content-addressed
path under `REVU_INDEX_STORAGE_DIR`. `db.BranchIndex.graph_ref` (a
`String(512)`) stores that path. If a later stage needs structural SQL
queries, normalised node/edge tables can be added without touching this
module's callers — `to_branch_index_fields` below only touches the columns
that already exist.

**Why this module doesn't import `db.BranchIndex` directly:** `packages/db`
depends on `packages/engine` (`revu`) for the Pydantic contracts it stores,
so the reverse import would be circular. Instead, `to_branch_index_fields`
returns a plain dict shaped to match `BranchIndex`'s columns; the actual ORM
row lifecycle (create/update, staleness transitions, force-push rebuild
detection) is Stage 5's job per `IMPLEMENTATION_PLAN.md`'s stage map, run
from `apps/worker` where both `revu` and `db` are already available. See
`packages/db/tests/test_index_store_integration.py` for a real-Postgres
test proving this dict round-trips through the actual `BranchIndex` model.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import pickle
from pathlib import Path
from typing import Any

import rustworkx as rx

DEFAULT_STORAGE_DIR_ENV = "REVU_INDEX_STORAGE_DIR"
DEFAULT_STORAGE_DIR = ".revu/index-graphs"


def default_storage_dir() -> Path:
    return Path(os.environ.get(DEFAULT_STORAGE_DIR_ENV, DEFAULT_STORAGE_DIR))


def _blob_filename(repo_identifier: str, branch_name: str, head_sha: str) -> str:
    # repo_identifier/branch_name can contain slashes ("org/repo",
    # "feature/x"); hash them into a filesystem-safe, collision-resistant name.
    key = f"{repo_identifier}:{branch_name}:{head_sha}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{digest}.graph.pkl.gz"


def save_graph(
    graph: rx.PyDiGraph,
    *,
    repo_identifier: str,
    branch_name: str,
    head_sha: str,
    storage_dir: Path | None = None,
) -> Path:
    """Serialise `graph` to disk and return the path written (the value to
    store in `BranchIndex.graph_ref`)."""
    storage_dir = storage_dir or default_storage_dir()
    storage_dir.mkdir(parents=True, exist_ok=True)
    path = storage_dir / _blob_filename(repo_identifier, branch_name, head_sha)
    payload = pickle.dumps(graph)
    path.write_bytes(gzip.compress(payload))
    return path


def load_graph(path: Path) -> rx.PyDiGraph:
    """Load a graph previously written by `save_graph`."""
    payload = gzip.decompress(path.read_bytes())
    graph = pickle.loads(payload)  # noqa: S301 — trusted, locally-produced file, not user input
    if not isinstance(graph, rx.PyDiGraph):
        raise TypeError(f"expected a PyDiGraph blob at {path}, got {type(graph)!r}")
    return graph


def to_branch_index_fields(
    *,
    node_count: int,
    edge_count: int,
    graph_ref: str,
    unresolved: list[dict[str, Any]],
    build_duration_ms: int,
    max_unresolved_logged: int = 500,
) -> dict[str, Any]:
    """Map indexer output onto `db.BranchIndex`'s column names.

    `unresolved` is capped at `max_unresolved_logged` entries — a large,
    low-signal-per-item error log has no business bloating a JSONB column;
    the full list is always available by re-running the indexer or reading
    it straight from `IndexResult.unresolved` at build time.
    """
    return {
        "node_count": node_count,
        "edge_count": edge_count,
        "graph_ref": graph_ref,
        "unresolved_symbols": unresolved[:max_unresolved_logged],
        "build_duration_ms": build_duration_ms,
    }
