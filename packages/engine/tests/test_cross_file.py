"""Unit tests for the cross-file agent's own orchestration logic (the
LangGraph tool-calling loop, round-cap handling, final-JSON retry) — with
`complete()` and `build_context_bundle` mocked out, so these run offline and
in isolation from Stage 6's own (already-tested) retrieval logic. The real,
end-to-end, real-graph case lives in `test_cross_file_integration.py`.
"""

from pathlib import Path

import pytest
import rustworkx as rx
from revu.agents import cross_file
from revu.models import ContextBundle
from revu.providers.llm import CompletionResult, ToolCall


def _bundle() -> ContextBundle:
    return ContextBundle(items=[], total_tokens=0, retrieval_strategy="test")


def _result(
    content: str = "",
    *,
    tool_calls: list[ToolCall] | None = None,
    tokens_in: int = 10,
    tokens_out: int = 10,
    cost: float = 0.001,
    latency: int = 50,
) -> CompletionResult:
    return CompletionResult(
        content=content, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost,
        latency_ms=latency, tool_calls=tool_calls or [],
    )


def _valid_findings_json(count: int = 1) -> str:
    findings = [
        {
            "file_path": "a.py", "line_start": 1, "line_end": 2,
            "category": "correctness", "severity": "high",
            "message": "caller not updated", "confidence": 0.9,
            "evidence": [{"file_path": "b.py", "line_start": 5, "line_end": 5, "reason": "caller"}],
        }
        for _ in range(count)
    ]
    import json

    return json.dumps({"findings": findings})


@pytest.fixture(autouse=True)
def _mock_context_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cross_file, "build_context_bundle", lambda *a, **k: _bundle())


async def test_answers_immediately_with_no_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_complete(**kwargs: object) -> CompletionResult:
        return _result(_valid_findings_json())

    monkeypatch.setattr(cross_file, "complete", fake_complete)

    result = await cross_file.review_cross_file(
        pr_title="t", pr_body="b", diff="d", repo_root=Path("."), graph=rx.PyDiGraph(),
        model="fake-model",
    )

    assert len(result.findings) == 1
    assert result.findings[0].agent_name == "cross_file"
    assert result.findings[0].evidence[0].reason == "caller"
    assert result.stopped_reason is None


async def test_one_tool_call_round_then_final_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def fake_complete(
        *, messages: list[dict[str, object]], **kwargs: object
    ) -> CompletionResult:
        calls.append(messages)
        if len(calls) == 1:
            return _result(
                "",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="find_definition",
                        arguments={"qualified_name": "x.y"},
                        raw_arguments='{"qualified_name": "x.y"}',
                    )
                ],
            )
        return _result(_valid_findings_json())

    monkeypatch.setattr(cross_file, "complete", fake_complete)

    result = await cross_file.review_cross_file(
        pr_title="t", pr_body="b", diff="d", repo_root=Path("."), graph=rx.PyDiGraph(),
        model="fake-model",
    )

    assert len(calls) == 2
    # The second call's message history includes a tool-result message.
    assert any(m.get("role") == "tool" for m in calls[1])
    assert len(result.findings) == 1
    assert result.stopped_reason is None
    # Tokens/cost sum across both rounds.
    assert result.tokens_in == 20
    assert result.tokens_out == 20


async def test_hitting_max_rounds_while_still_requesting_tools_sets_stopped_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def always_wants_a_tool(**kwargs: object) -> CompletionResult:
        return _result(
            "",
            tool_calls=[
                ToolCall(
                    id="call_x",
                    name="find_definition",
                    arguments={"qualified_name": "x"},
                    raw_arguments='{"qualified_name": "x"}',
                )
            ],
        )

    monkeypatch.setattr(cross_file, "complete", always_wants_a_tool)

    result = await cross_file.review_cross_file(
        pr_title="t", pr_body="b", diff="d", repo_root=Path("."), graph=rx.PyDiGraph(),
        model="fake-model", max_rounds=3,
    )

    assert result.findings == []
    assert result.stopped_reason == "max_tool_rounds_reached (3)"


async def test_unknown_tool_name_does_not_crash_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def fake_complete(
        *, messages: list[dict[str, object]], **kwargs: object
    ) -> CompletionResult:
        calls.append(messages)
        if len(calls) == 1:
            return _result(
                "",
                tool_calls=[
                    ToolCall(id="call_1", name="not_a_real_tool", arguments={}, raw_arguments="{}")
                ],
            )
        return _result(_valid_findings_json(0))

    monkeypatch.setattr(cross_file, "complete", fake_complete)

    result = await cross_file.review_cross_file(
        pr_title="t", pr_body="b", diff="d", repo_root=Path("."), graph=rx.PyDiGraph(),
        model="fake-model",
    )

    tool_message = next(m for m in calls[1] if m.get("role") == "tool")
    assert "error" in tool_message["content"]
    assert result.stopped_reason is None
    assert result.findings == []


async def test_retries_once_on_malformed_final_json_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_complete(
        *, messages: list[dict[str, object]], **kwargs: object
    ) -> CompletionResult:
        calls.append(messages)
        if len(calls) == 1:
            return _result("not json at all")
        return _result(_valid_findings_json())

    monkeypatch.setattr(cross_file, "complete", fake_complete)

    result = await cross_file.review_cross_file(
        pr_title="t", pr_body="b", diff="d", repo_root=Path("."), graph=rx.PyDiGraph(),
        model="fake-model",
    )

    assert len(calls) == 2
    assert len(result.findings) == 1
    assert result.stopped_reason is None


async def test_gives_up_after_second_malformed_response(monkeypatch: pytest.MonkeyPatch) -> None:
    async def always_malformed(**kwargs: object) -> CompletionResult:
        return _result("still not json")

    monkeypatch.setattr(cross_file, "complete", always_malformed)

    result = await cross_file.review_cross_file(
        pr_title="t", pr_body="b", diff="d", repo_root=Path("."), graph=rx.PyDiGraph(),
        model="fake-model",
    )

    assert result.findings == []
    assert result.stopped_reason == "final_answer_malformed_after_retry"


async def test_context_bundle_is_attached_to_the_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_complete(**kwargs: object) -> CompletionResult:
        return _result(_valid_findings_json(0))

    monkeypatch.setattr(cross_file, "complete", fake_complete)

    result = await cross_file.review_cross_file(
        pr_title="t", pr_body="b", diff="d", repo_root=Path("."), graph=rx.PyDiGraph(),
        model="fake-model",
    )

    assert result.context_bundle is not None
    assert result.context_bundle.retrieval_strategy == "test"
