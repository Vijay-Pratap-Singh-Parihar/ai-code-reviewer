"""Stage 8's hand-verified acceptance check for evidence resolution, in the
same spirit as Stage 4's "10 hand-verified call edges"
(`packages/engine/tests/index/test_verified_edges.py`) and Stage 6/7's
real-graph integration tests: build a *real* index of this repository's own
working tree (via the shared `real_repo_index`/`real_repo_root` fixtures -
see `packages/engine/tests/conftest.py`, added in Stage 7 specifically so no
test adds a tenth redundant full-repo build), then prove the
evidence-resolution mechanism (`revu.verify.evidence.resolve_evidence`)
actually distinguishes a genuine citation from a fabricated one - the
roadmap's own framing for why this step exists: "log the drop rate - it
measures hallucination directly."

**What this test does and does not prove, stated honestly:** it proves the
*resolution mechanism itself* works correctly against real data - a real
file/line citation (read from this repository's actual, real indexed graph)
resolves and survives, while a fabricated one (a file path that does not
exist, and separately a real file with a wildly out-of-range line number)
is correctly dropped, and the reported drop rate reflects exactly that. It
says **nothing** about real-world hallucination rates from actual LLMs -
that would need real model output, which this stage is forbidden from
generating (no LLM calls, per `IMPLEMENTATION_PLAN.md`'s explicit
constraint for Stage 8).
"""

from pathlib import Path

from revu.index.graph import IndexResult
from revu.models import EvidenceItem, Finding, FindingCategory, Severity
from revu.verify.evidence import resolve_evidence

# `revu.models.Finding` - the shared Pydantic contract itself - is used as
# the real, hand-verified location below because it is one of the oldest,
# most stable symbols in this repository (present since Stage 1), making it
# an honest, low-drift choice for a fixture that must keep working as the
# codebase evolves.
REAL_QUALIFIED_NAME = "revu.models.Finding"


def _finding_citing(
    *, own_file: str, own_line_start: int, own_line_end: int, evidence: EvidenceItem
) -> Finding:
    return Finding(
        file_path=own_file,
        line_start=own_line_start,
        line_end=own_line_end,
        category=FindingCategory.MAINTAINABILITY,
        severity=Severity.LOW,
        message="placeholder finding for the evidence-resolution acceptance check",
        evidence=[evidence],
        confidence=0.5,
        agent_name="test",
    )


def test_evidence_resolution_distinguishes_real_from_fabricated_citations(
    real_repo_root: Path, real_repo_index: IndexResult
) -> None:
    symbols_by_qualified_name = {s.qualified_name: s for s in real_repo_index.symbols}
    real_symbol = symbols_by_qualified_name[REAL_QUALIFIED_NAME]

    # Hand-verify the indexer's own claim before trusting it as "real": read
    # the actual file at the line it reports and confirm it genuinely is the
    # class definition - not accepted by construction.
    real_source_lines = (real_repo_root / real_symbol.file_path).read_text(
        encoding="utf-8"
    ).splitlines()
    claimed_line = real_source_lines[real_symbol.line_start - 1]
    assert "class Finding" in claimed_line, (
        f"expected the indexer's reported line {real_symbol.line_start} of "
        f"{real_symbol.file_path} to be the real `class Finding` definition, got: "
        f"{claimed_line!r}"
    )

    real_finding = _finding_citing(
        own_file=real_symbol.file_path,
        own_line_start=real_symbol.line_start,
        own_line_end=real_symbol.line_end,
        evidence=EvidenceItem(
            file_path=real_symbol.file_path,
            line_start=real_symbol.line_start,
            line_end=real_symbol.line_end,
            reason="the real, hand-verified `Finding` class definition",
        ),
    )

    fabricated_missing_file = _finding_citing(
        own_file=real_symbol.file_path,
        own_line_start=real_symbol.line_start,
        own_line_end=real_symbol.line_end,
        evidence=EvidenceItem(
            file_path="revu/this_module_does_not_exist_anywhere.py",
            line_start=1,
            line_end=5,
            reason="a hallucinated file path that has never existed in this repository",
        ),
    )

    fabricated_out_of_range = _finding_citing(
        own_file=real_symbol.file_path,
        own_line_start=real_symbol.line_start,
        own_line_end=real_symbol.line_end,
        evidence=EvidenceItem(
            file_path=real_symbol.file_path,
            line_start=1,
            line_end=len(real_source_lines) + 5000,
            reason="a real file, but a line number far beyond its actual length",
        ),
    )

    result = resolve_evidence(
        [real_finding, fabricated_missing_file, fabricated_out_of_range],
        repo_root=real_repo_root,
    )

    # The mechanism's core claim: the genuine citation survives, both
    # flavors of fabricated citation are dropped, and the reported drop rate
    # is exactly 2/3 - real, inspectable data, not just a log line.
    assert result.survived == [real_finding]
    assert result.dropped_count == 2
    assert result.total_count == 3
    assert result.drop_rate == 2 / 3

    reports_by_id = {id(r.original_finding): r for r in result.reports}
    assert reports_by_id[id(real_finding)].all_resolved is True
    assert reports_by_id[id(fabricated_missing_file)].evidence_resolutions[0].resolved is False
    assert reports_by_id[id(fabricated_out_of_range)].evidence_resolutions[0].resolved is False
