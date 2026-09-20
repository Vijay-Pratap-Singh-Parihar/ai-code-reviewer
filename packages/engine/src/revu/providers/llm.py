"""LiteLLM-backed completion wrapper with normalised token/cost accounting.

Per Product_Architecture_FullStack.md §7: route every provider through one
interface, and convert usage to a common (tokens_in, tokens_out, cost_usd)
record at ingestion so the ledger is comparable across providers.
"""

import time
from dataclasses import dataclass

import litellm

# Some models (e.g. Claude's newer reasoning-oriented models) only support a
# fixed temperature and reject an explicit temperature=0.0 with a hard error.
# Providers differ on which params they accept; silently dropping the ones a
# given model doesn't support beats failing every call to that model.
litellm.drop_params = True


@dataclass(frozen=True)
class CompletionResult:
    content: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int


async def complete(
    *,
    model: str,
    messages: list[dict[str, str]],
    response_format: dict[str, object] | None = None,
    temperature: float = 0.0,
) -> CompletionResult:
    start = time.monotonic()
    response = await litellm.acompletion(
        model=model,
        messages=messages,
        response_format=response_format,
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

    content = response.choices[0].message.content or ""

    return CompletionResult(
        content=content,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=float(cost_usd),
        latency_ms=latency_ms,
    )
