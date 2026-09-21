"""Acceptance check from the roadmap: 10 hand-verified call relationships
from a real repository must resolve in the indexed graph.

Uses this very repository as the subject — it's real, non-trivial Python
code with genuine cross-file/cross-package calls (API router -> service
layer -> ORM model, worker -> engine), and needs no network access to
fetch an external repo. Each edge in `verified_edges.yaml` was confirmed by
hand against the actual source line before being added there.
"""

from pathlib import Path

import yaml
from revu.index.graph import build_index_at_path

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "verified_edges.yaml"


def _load_verified_edges() -> list[dict[str, object]]:
    data = yaml.safe_load(FIXTURE_PATH.read_text())
    edges = data["edges"]
    assert isinstance(edges, list)
    return edges


def test_ten_hand_verified_edges_file_has_at_least_ten_entries() -> None:
    edges = _load_verified_edges()
    assert len(edges) >= 10


def test_all_hand_verified_call_edges_resolve_in_the_graph() -> None:
    edges = _load_verified_edges()
    result = build_index_at_path(REPO_ROOT)
    resolved_pairs = {(e.caller, e.callee) for e in result.call_edges}

    failures = []
    for entry in edges:
        pair = (entry["caller"], entry["callee"])
        if pair not in resolved_pairs:
            failures.append(entry)

    assert not failures, (
        f"{len(failures)}/{len(edges)} hand-verified edges did not resolve: {failures}"
    )


def test_hand_verified_call_sites_are_at_the_claimed_line() -> None:
    """Belt-and-braces: confirm the call site really is on the recorded line
    in the recorded file, so the fixture can't silently drift from the
    source it claims to describe.
    """
    edges = _load_verified_edges()
    for entry in edges:
        file_path = REPO_ROOT / str(entry["file"])
        lines = file_path.read_text(encoding="utf-8").splitlines()
        line_text = lines[int(entry["line"]) - 1]
        callee_short_name = str(entry["callee"]).rsplit(".", 1)[-1]
        assert callee_short_name in line_text, (
            f"{entry['callee']} not textually present on {entry['file']}:{entry['line']}: "
            f"{line_text!r}"
        )
