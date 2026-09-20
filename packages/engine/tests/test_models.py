import pytest
from pydantic import ValidationError
from revu.models import (
    ContextBundle,
    ContextItem,
    EvidenceItem,
    Finding,
    FindingCategory,
    RunResult,
    Severity,
)


def _make_finding(**overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "file_path": "src/app.py",
        "line_start": 10,
        "line_end": 12,
        "category": FindingCategory.CORRECTNESS,
        "severity": Severity.HIGH,
        "message": "off-by-one in loop bound",
        "confidence": 0.8,
        "agent_name": "diff_only",
    }
    defaults.update(overrides)
    return Finding(**defaults)  # type: ignore[arg-type]


def test_finding_accepts_valid_confidence() -> None:
    finding = _make_finding(confidence=0.0)
    assert finding.confidence == 0.0
    finding = _make_finding(confidence=1.0)
    assert finding.confidence == 1.0


@pytest.mark.parametrize("bad_confidence", [-0.01, 1.01, 2.0, -5.0])
def test_finding_rejects_confidence_out_of_bounds(bad_confidence: float) -> None:
    with pytest.raises(ValidationError):
        _make_finding(confidence=bad_confidence)


def test_finding_evidence_defaults_to_empty_list() -> None:
    finding = _make_finding()
    assert finding.evidence == []


def test_finding_carries_evidence_trail() -> None:
    evidence = EvidenceItem(
        file_path="src/caller.py",
        line_start=4,
        line_end=4,
        reason="calls the changed function",
    )
    finding = _make_finding(evidence=[evidence])
    assert finding.evidence[0].reason == "calls the changed function"


def test_context_bundle_defaults() -> None:
    bundle = ContextBundle(retrieval_strategy="graph_khop")
    assert bundle.items == []
    assert bundle.total_tokens == 0


def test_context_bundle_holds_items() -> None:
    item = ContextItem(
        file_path="src/util.py",
        line_start=1,
        line_end=5,
        content="def helper(): ...",
        retrieval_reason="1-hop callee of changed function",
        score=0.92,
    )
    bundle = ContextBundle(items=[item], total_tokens=42, retrieval_strategy="graph_khop")
    assert bundle.total_tokens == 42
    assert bundle.items[0].score == pytest.approx(0.92)


def test_run_result_defaults_have_no_findings_or_cost() -> None:
    result = RunResult()
    assert result.findings == []
    assert result.context_bundle is None
    assert result.cost_usd == 0.0


def test_run_result_aggregates_findings() -> None:
    finding = _make_finding()
    result = RunResult(findings=[finding], tokens_in=100, tokens_out=50, cost_usd=0.01)
    assert len(result.findings) == 1
    assert result.tokens_in == 100
