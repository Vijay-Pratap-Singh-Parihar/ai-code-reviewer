"""LiteLLM-backed completion wrapper with normalised token/cost accounting.

Per Product_Architecture_FullStack.md §7: route every provider through one
interface, and convert usage to a common (tokens_in, tokens_out, cost_usd)
record at ingestion so the ledger is comparable across providers.
"""

import json
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
    model: str,
    messages: list[dict[str, Any]],
    response_format: dict[str, object] | None = None,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.0,
) -> CompletionResult:
    start = time.monotonic()
    response = await litellm.acompletion(
        model=model,
        messages=messages,
        response_format=response_format,
        tools=tools,
        temperature=temperature,
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
