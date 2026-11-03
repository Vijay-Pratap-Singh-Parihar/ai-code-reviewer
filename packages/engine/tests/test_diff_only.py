import json

import pytest
from revu.agents import diff_only
from revu.providers.llm import CompletionResult


def _result(
    content: str, tokens_in: int = 10, tokens_out: int = 10, cost: float = 0.001, latency: int = 50
) -> CompletionResult:
    return CompletionResult(
        content=content,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
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
        *,
        model: str,
        messages: list[dict[str, str]],
        response_format: object = None,
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


def _file(path: str) -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,0 +1,1 @@\n+x = 1\n"


def _finding_for(path: str) -> str:
    finding = {
        "file_path": path,
        "line_start": 1,
        "line_end": 1,
        "category": "correctness",
        "severity": "low",
        "message": "m",
        "confidence": 0.5,
    }
    return json.dumps({"findings": [finding]})


async def test_a_large_diff_is_reviewed_in_parts_and_the_results_combined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diff = _file("a.py") + _file("b.py") + _file("c.py")
    prompts: list[str] = []

    async def fake_complete(
        *, messages: list[dict[str, str]], **kwargs: object
    ) -> CompletionResult:
        prompt = messages[-1]["content"]
        prompts.append(prompt)
        path = next(p for p in ("a.py", "b.py", "c.py") if f"+++ b/{p}" in prompt)
        return _result(_finding_for(path))

    monkeypatch.setattr(diff_only, "complete", fake_complete)

    result = await diff_only.review_diff(
        pr_title="t", pr_body="b", diff=diff, model="fake", max_part_chars=len(_file("a.py"))
    )

    assert result.parts == 3
    assert sorted(f.file_path for f in result.findings) == ["a.py", "b.py", "c.py"]
    assert result.tokens_in == 30 and result.cost_usd == pytest.approx(0.003)
    assert all("of 3; the other files are reviewed separately" in p for p in prompts)


async def test_lockfiles_are_never_sent_to_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    async def fake_complete(
        *, messages: list[dict[str, str]], **kwargs: object
    ) -> CompletionResult:
        sent.append(messages[-1]["content"])
        return _result('{"findings": []}')

    monkeypatch.setattr(diff_only, "complete", fake_complete)

    only_lock = await diff_only.review_diff(
        pr_title="t", pr_body="b", diff=_file("yarn.lock"), model="fake"
    )
    mixed = await diff_only.review_diff(
        pr_title="t", pr_body="b", diff=_file("yarn.lock") + _file("a.py"), model="fake"
    )

    assert only_lock.parts == 0 and only_lock.skipped_files == ["yarn.lock"]
    assert mixed.skipped_files == ["yarn.lock"]
    assert len(sent) == 1 and "yarn.lock" not in sent[0]


async def test_an_answer_cut_off_at_the_token_limit_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_complete(**kwargs: object) -> CompletionResult:
        nonlocal calls
        calls += 1
        return CompletionResult(
            content='{"findings": [{"file_path": "a.py", "line_st',
            tokens_in=10,
            tokens_out=4096,
            cost_usd=0.0,
            latency_ms=1,
            finish_reason="length",
        )

    monkeypatch.setattr(diff_only, "complete", fake_complete)

    result = await diff_only.review_diff(pr_title="t", pr_body="b", diff=_file("a.py"), model="f")

    assert calls == 1
    assert result.findings == [] and result.stopped_reason == "output_token_limit"


async def test_a_failed_part_loses_only_its_own_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_complete(
        *, messages: list[dict[str, str]], **kwargs: object
    ) -> CompletionResult:
        if "+++ b/b.py" in messages[-1]["content"]:
            raise TimeoutError("model too slow")
        return _result(_finding_for("a.py"))

    monkeypatch.setattr(diff_only, "complete", fake_complete)
    diff = _file("a.py") + _file("b.py")

    result = await diff_only.review_diff(
        pr_title="t", pr_body="b", diff=diff, model="f", max_part_chars=len(_file("a.py"))
    )

    assert [f.file_path for f in result.findings] == ["a.py"]
    assert result.stopped_reason == "parts_failed (1 of 2)"
    with pytest.raises(TimeoutError):  # nothing to show at all: the run fails
        await diff_only.review_diff(pr_title="t", pr_body="b", diff=_file("b.py"), model="f")
