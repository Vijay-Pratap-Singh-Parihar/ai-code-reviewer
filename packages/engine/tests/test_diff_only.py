import pytest
from revu.agents import diff_only
from revu.providers.llm import CompletionResult


def _result(
    content: str, tokens_in: int = 10, tokens_out: int = 10, cost: float = 0.001, latency: int = 50
) -> CompletionResult:
    return CompletionResult(
        content=content, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost,
        latency_ms=latency,
    )


async def test_review_diff_parses_valid_json_on_first_try(monkeypatch: pytest.MonkeyPatch) -> None:
    valid_json = (
        '{"findings": [{"file_path": "a.py", "line_start": 1, "line_end": 2, '
        '"category": "correctness", "severity": "high", "message": "bug", "confidence": 0.9}]}'
    )

    async def fake_complete(**kwargs: object) -> CompletionResult:
        return _result(valid_json)

    monkeypatch.setattr(diff_only, "complete", fake_complete)

    result = await diff_only.review_diff(pr_title="t", pr_body="b", diff="d", model="fake")

    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.file_path == "a.py"
    assert finding.agent_name == "diff_only"
    assert finding.evidence == []
    assert result.tokens_in == 10
    assert result.tokens_out == 10


async def test_review_diff_returns_empty_list_on_empty_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_complete(**kwargs: object) -> CompletionResult:
        return _result('{"findings": []}')

    monkeypatch.setattr(diff_only, "complete", fake_complete)

    result = await diff_only.review_diff(pr_title="t", pr_body="b", diff="d", model="fake")
    assert result.findings == []


async def test_review_diff_retries_once_on_malformed_json_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[dict[str, str]]] = []

    async def fake_complete(
        *, model: str, messages: list[dict[str, str]], response_format: object = None,
        temperature: float = 0.0,
    ) -> CompletionResult:
        calls.append(messages)
        if len(calls) == 1:
            return _result("not json at all", tokens_in=5, tokens_out=5)
        return _result(
            '{"findings": [{"file_path": "b.py", "line_start": 3, "line_end": 3, '
            '"category": "security", "severity": "critical", "message": "sqli", '
            '"confidence": 0.5}]}',
            tokens_in=7,
            tokens_out=7,
        )

    monkeypatch.setattr(diff_only, "complete", fake_complete)

    result = await diff_only.review_diff(pr_title="t", pr_body="b", diff="d", model="fake")

    assert len(calls) == 2
    assert len(result.findings) == 1
    assert result.findings[0].category.value == "security"
    # Token accounting must sum both attempts, not just the successful one.
    assert result.tokens_in == 12
    assert result.tokens_out == 12


async def test_review_diff_gives_up_after_second_malformed_response(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def fake_complete(**kwargs: object) -> CompletionResult:
        return _result("still not json")

    monkeypatch.setattr(diff_only, "complete", fake_complete)

    with caplog.at_level("ERROR"):
        result = await diff_only.review_diff(pr_title="t", pr_body="b", diff="d", model="fake")

    assert result.findings == []
    assert any("giving up" in message for message in caplog.messages)


async def test_review_diff_treats_schema_violation_as_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid JSON that doesn't match the Finding schema (confidence out of
    [0, 1]) must be treated the same as unparseable JSON, not raise.
    """
    invalid_confidence_json = (
        '{"findings": [{"file_path": "a.py", "line_start": 1, "line_end": 1, '
        '"category": "correctness", "severity": "low", "message": "x", "confidence": 5.0}]}'
    )

    async def fake_complete(**kwargs: object) -> CompletionResult:
        return _result(invalid_confidence_json)

    monkeypatch.setattr(diff_only, "complete", fake_complete)

    result = await diff_only.review_diff(pr_title="t", pr_body="b", diff="d", model="fake")
    assert result.findings == []
