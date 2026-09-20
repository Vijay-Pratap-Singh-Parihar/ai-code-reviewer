import pytest
from revu.providers import llm


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeResponse:
    def __init__(self, content: str, prompt_tokens: int = 10, completion_tokens: int = 5) -> None:
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage(prompt_tokens, completion_tokens)


async def test_complete_extracts_content_tokens_and_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_response = _FakeResponse("hello world", prompt_tokens=100, completion_tokens=20)

    async def fake_acompletion(**kwargs: object) -> _FakeResponse:
        return fake_response

    monkeypatch.setattr(llm.litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(llm.litellm, "completion_cost", lambda **kwargs: 0.0042)

    result = await llm.complete(model="fake-model", messages=[{"role": "user", "content": "hi"}])

    assert result.content == "hello world"
    assert result.tokens_in == 100
    assert result.tokens_out == 20
    assert result.cost_usd == pytest.approx(0.0042)
    assert result.latency_ms >= 0


async def test_complete_handles_missing_cost_data_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_response = _FakeResponse("ok")

    async def fake_acompletion(**kwargs: object) -> _FakeResponse:
        return fake_response

    def raise_cost_error(**kwargs: object) -> float:
        raise ValueError("no cost data for this model")

    monkeypatch.setattr(llm.litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(llm.litellm, "completion_cost", raise_cost_error)

    result = await llm.complete(model="fake-model", messages=[{"role": "user", "content": "hi"}])

    assert result.cost_usd == 0.0


async def test_complete_passes_through_model_and_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_acompletion(**kwargs: object) -> _FakeResponse:
        captured.update(kwargs)
        return _FakeResponse("x")

    monkeypatch.setattr(llm.litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(llm.litellm, "completion_cost", lambda **kwargs: 0.0)

    messages = [{"role": "user", "content": "review this"}]
    await llm.complete(model="claude-sonnet-5", messages=messages, temperature=0.2)

    assert captured["model"] == "claude-sonnet-5"
    assert captured["messages"] == messages
    assert captured["temperature"] == 0.2
