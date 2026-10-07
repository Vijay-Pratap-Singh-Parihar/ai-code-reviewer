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


async def test_an_endpoint_passes_its_own_key_url_and_headers_to_litellm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    async def fake_acompletion(**kwargs: object) -> _FakeResponse:
        seen.update(kwargs)
        return _FakeResponse("ok", prompt_tokens=1_000_000, completion_tokens=500_000)

    monkeypatch.setattr(llm.litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(llm.litellm, "completion_cost", lambda **kwargs: 0.0)
    endpoint = llm.ModelEndpoint(
        model="openai/qwen3.5:9b",
        api_key="not-needed",
        api_base="http://127.0.0.1:11434/v1",
        extra_headers={"X-Gateway": "team-a"},
        input_cost_per_mtok=0.2,
        output_cost_per_mtok=0.6,
    )

    result = await llm.complete(
        model=endpoint, messages=[{"role": "user", "content": "hi"}], max_tokens=64, timeout=5
    )

    assert seen["model"] == "openai/qwen3.5:9b"
    assert seen["api_key"] == "not-needed"
    assert seen["api_base"] == "http://127.0.0.1:11434/v1"
    assert seen["extra_headers"] == {"X-Gateway": "team-a"}
    assert (seen["max_tokens"], seen["timeout"]) == (64, 5)
    # LiteLLM has no price for a self-hosted model: the configured one is used.
    assert result.cost_usd == pytest.approx(0.2 + 0.3)


def test_an_endpoint_never_shows_its_key() -> None:
    endpoint = llm.ModelEndpoint(model="groq/m", api_key="gsk_secret_value")
    assert "gsk_secret_value" not in repr(endpoint)


@pytest.mark.parametrize(
    "content",
    [
        '{"findings": []}',
        '<think>The diff changes a loop bound...</think>\n{"findings": []}',
        '```json\n{"findings": []}\n```',
        'Here is my review:\n{"findings": []}\nLet me know if you need more.',
    ],
)
def test_extract_json_tolerates_what_open_models_wrap_answers_in(content: str) -> None:
    assert llm.extract_json(content) == {"findings": []}


def test_extract_json_still_fails_when_there_is_no_object() -> None:
    import json

    with pytest.raises(json.JSONDecodeError):
        llm.extract_json("I could not find any issues.")
