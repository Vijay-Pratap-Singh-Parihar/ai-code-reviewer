"""LiteLLM-backed completion wrapper with normalised token/cost accounting.

Per Product_Architecture_FullStack.md §7: route every provider through one
interface, and convert usage to a common (tokens_in, tokens_out, cost_usd)
record at ingestion so the ledger is comparable across providers.
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import litellm

# Some models (e.g. Claude's newer reasoning-oriented models) only support a
# fixed temperature and reject an explicit temperature=0.0. Providers differ
# on which params they accept; silently dropping the ones a given model
# doesn't support beats failing every call to that model.
litellm.drop_params = True


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
    input_cost_per_mtok: float | None = None
    output_cost_per_mtok: float | None = None

    def __repr__(self) -> str:  # never print the key in logs or tracebacks
        return f"ModelEndpoint(model={self.model!r}, api_base={self.api_base!r})"


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
                id=call.id, name=call.function.name, arguments=arguments,
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
    kwargs: dict[str, Any] = {}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if timeout is not None:
        kwargs["timeout"] = timeout
    if endpoint is not None:
        kwargs["api_key"] = endpoint.api_key
        if endpoint.api_base:
            kwargs["api_base"] = endpoint.api_base
        if endpoint.extra_headers:
            kwargs["extra_headers"] = endpoint.extra_headers

    start = time.monotonic()
    response = await litellm.acompletion(
        model=endpoint.model if endpoint else model,
        messages=messages,
        response_format=response_format,
        tools=tools,
        temperature=temperature,
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

    message = response.choices[0].message
    content = message.content or ""
    tool_calls = _parse_tool_calls(message)

    return CompletionResult(
        content=content,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=float(cost_usd),
        latency_ms=latency_ms,
        tool_calls=tool_calls,
    )


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def extract_json(content: str) -> Any:
    """Parse a model's JSON answer, tolerating what open models commonly wrap
    it in: a `<think>...</think>` reasoning block, a ```json code fence, or
    a sentence before/after the object. Raises `json.JSONDecodeError` when
    there is no JSON object to be found."""
    text = _THINK_BLOCK.sub("", content).strip()
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
