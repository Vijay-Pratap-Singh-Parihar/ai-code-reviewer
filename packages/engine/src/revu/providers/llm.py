"""LiteLLM-backed completion wrapper with normalised token/cost accounting.

Per Product_Architecture_FullStack.md §7: route every provider through one
interface, and convert usage to a common (tokens_in, tokens_out, cost_usd)
record at ingestion so the ledger is comparable across providers.
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

# LiteLLM downloads its model price table from GitHub on import, retrying for
# ~20 s when offline. Use the copy it ships with instead: startup stays fast
# and deterministic, and an air-gapped or offline deployment (a local model,
# a demo without internet) never waits on GitHub. Set before the import.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm  # noqa: E402
from litellm.exceptions import (  # noqa: E402
    APIConnectionError,
    AuthenticationError,
    RateLimitError,
    Timeout,
)

# Some models (e.g. Claude's newer reasoning-oriented models) only support a
# fixed temperature and reject an explicit temperature=0.0. Providers differ
# on which params they accept; silently dropping the ones a given model
# doesn't support beats failing every call to that model.
litellm.drop_params = True


# How much a model may think before answering, chosen per review step in the
# UI. `None` leaves it to the model (many open models think by default).
Reasoning = Literal["off", "low", "medium", "high"]
REASONING_LEVELS: tuple[str, ...] = get_args(Reasoning)

# Anthropic needs room for the thinking budget inside `max_tokens`
# (LiteLLM's budget for "high" is 4096 tokens), plus the answer itself.
_ANTHROPIC_THINKING_MAX_TOKENS = 16_000

# Every call is bounded, so a model that never stops (small local models can
# repeat themselves indefinitely) ends in minutes, not hours. A review's JSON
# answer fits easily in the first limit; a model that thinks first gets room
# for its reasoning too, which providers count against the same limit.
ANSWER_MAX_TOKENS = 4_096
THINKING_MAX_TOKENS = 16_000
DEFAULT_TIMEOUT_S = 600.0


def default_max_tokens(endpoint: "ModelEndpoint | None") -> int:
    if endpoint is not None and endpoint.reasoning == "off":
        return ANSWER_MAX_TOKENS
    return THINKING_MAX_TOKENS


@dataclass(frozen=True)
class ModelEndpoint:
    """Everything needed to call one model, resolved from an organisation's
    AI provider configuration (never from environment variables).

    `model` is the LiteLLM model string (`anthropic/claude-sonnet-5`,
    `groq/openai/gpt-oss-120b`, `openai/qwen3.5:9b` with `api_base` for an
    OpenAI-compatible server). `api_key` is always passed explicitly, even
    as a placeholder for servers that need none, so LiteLLM never falls back
    to a key that happens to be in the process environment.

    Prices are per million tokens and only used when LiteLLM has no price
    table for the model (self-hosted and fine-tuned models), so cost
    accounting still works for them.
    """

    model: str
    api_key: str
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None
    supports_tools: bool = True
    reasoning: Reasoning | None = None
    input_cost_per_mtok: float | None = None
    output_cost_per_mtok: float | None = None

    def __repr__(self) -> str:  # never print the key in logs or tracebacks
        return f"ModelEndpoint(model={self.model!r}, api_base={self.api_base!r})"


def reasoning_kwargs(endpoint: ModelEndpoint, *, max_tokens: int | None) -> dict[str, Any]:
    """The completion arguments that apply `endpoint.reasoning`.

    Every supported provider takes the OpenAI-style `reasoning_effort`, which
    LiteLLM translates (an Anthropic thinking budget, Groq's and Ollama's own
    switch). "off" is sent as `none` rather than left out, because open
    models such as Qwen think unless told not to: on a 4 GB laptop GPU that
    is the difference between a 1-second and a 5-minute answer.

    Anthropic and OpenAI's hosted reasoning models refuse a custom
    temperature while thinking, so it is left out for them (`temperature`
    is `None` in the result); Anthropic also needs `max_tokens` to exceed
    the thinking budget.
    """
    level = endpoint.reasoning
    if level is None:
        return {}
    if level == "off":
        return {"reasoning_effort": "none"}
    kwargs: dict[str, Any] = {"reasoning_effort": level}
    hosted_reasoner = endpoint.api_base is None and endpoint.model.startswith(
        ("anthropic/", "openai/")
    )
    if hosted_reasoner:
        kwargs["temperature"] = None
    if (
        endpoint.model.startswith("anthropic/")
        and (max_tokens or 0) < _ANTHROPIC_THINKING_MAX_TOKENS
    ):
        kwargs["max_tokens"] = _ANTHROPIC_THINKING_MAX_TOKENS
    return kwargs


@dataclass(frozen=True)
class ToolCall:
    """One tool invocation the model requested. `arguments` is parsed from
    the model's JSON string — if the model produced malformed JSON for the
    arguments themselves (rare, but real), `arguments` is `{}` and
    `raw_arguments` keeps the original string so a caller can at least log
    what actually came back.
    """

    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str


@dataclass(frozen=True)
class CompletionResult:
    content: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None

    @property
    def truncated(self) -> bool:
        """The model hit `max_tokens` before finishing its answer."""
        return self.finish_reason == "length"


def _parse_tool_calls(message: Any) -> list[ToolCall]:
    raw_calls = getattr(message, "tool_calls", None) or []
    calls: list[ToolCall] = []
    for call in raw_calls:
        raw_arguments = call.function.arguments or "{}"
        try:
            arguments = json.loads(raw_arguments)
            if not isinstance(arguments, dict):
                arguments = {}
        except json.JSONDecodeError:
            arguments = {}
        calls.append(
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=arguments,
                raw_arguments=raw_arguments,
            )
        )
    return calls


async def complete(
    *,
    model: str | ModelEndpoint,
    messages: list[dict[str, Any]],
    response_format: dict[str, object] | None = None,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> CompletionResult:
    endpoint = model if isinstance(model, ModelEndpoint) else None
    if max_tokens is None:
        max_tokens = default_max_tokens(endpoint)
    kwargs: dict[str, Any] = {
        "max_tokens": max_tokens,
        "timeout": DEFAULT_TIMEOUT_S if timeout is None else timeout,
    }
    if endpoint is not None:
        kwargs["api_key"] = endpoint.api_key
        if endpoint.api_base:
            kwargs["api_base"] = endpoint.api_base
            # A self-hosted server that timed out is busy, not flaky: asking
            # again queues the same work behind itself and triples the wait.
            kwargs["max_retries"] = 0
        if endpoint.extra_headers:
            kwargs["extra_headers"] = endpoint.extra_headers
        kwargs.update(reasoning_kwargs(endpoint, max_tokens=max_tokens))
    kwargs.setdefault("temperature", temperature)
    if kwargs["temperature"] is None:
        del kwargs["temperature"]

    start = time.monotonic()
    response = await litellm.acompletion(
        model=endpoint.model if endpoint else model,
        messages=messages,
        response_format=response_format,
        tools=tools,
        **kwargs,
    )
    latency_ms = int((time.monotonic() - start) * 1000)

    usage = response.usage
    tokens_in = getattr(usage, "prompt_tokens", 0) or 0
    tokens_out = getattr(usage, "completion_tokens", 0) or 0

    try:
        cost_usd = litellm.completion_cost(completion_response=response)
    except Exception:
        # Cost tables don't cover every model/provider combination; a
        # missing price is not a reason to fail the whole completion.
        cost_usd = 0.0
    if not cost_usd and endpoint is not None and endpoint.input_cost_per_mtok is not None:
        cost_usd = (
            tokens_in * endpoint.input_cost_per_mtok
            + tokens_out * (endpoint.output_cost_per_mtok or 0.0)
        ) / 1_000_000

    choice = response.choices[0]
    message = choice.message
    content = message.content or ""
    tool_calls = _parse_tool_calls(message)

    return CompletionResult(
        content=content,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=float(cost_usd),
        latency_ms=latency_ms,
        tool_calls=tool_calls,
        finish_reason=getattr(choice, "finish_reason", None),
    )


def describe_error(exc: BaseException) -> str:
    """What a person should read about a failed model call: what happened
    and what to do, instead of the provider library's exception text."""
    if isinstance(exc, Timeout):
        return (
            "The model didn't answer in time. Local models on a CPU can be slow on "
            "large pull requests: try a smaller model, a GPU, or a hosted provider."
        )
    if isinstance(exc, AuthenticationError):
        return "The AI provider rejected its credentials. Check the key on the AI Providers page."
    if isinstance(exc, RateLimitError):
        return "The AI provider is rate limiting this key. Try again in a minute."
    if isinstance(exc, APIConnectionError):
        return "Couldn't reach the AI provider. Check its endpoint on the AI Providers page."
    return str(exc)

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def strip_thinking(content: str) -> str:
    """`content` without the `<think>...</think>` block open models put
    their reasoning in."""
    return _THINK_BLOCK.sub("", content)


def extract_json(content: str) -> Any:
    """Parse a model's JSON answer, tolerating what open models commonly wrap
    it in: a `<think>...</think>` reasoning block, a ```json code fence, or
    a sentence before/after the object. Raises `json.JSONDecodeError` when
    there is no JSON object to be found."""
    text = strip_thinking(content).strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])
