"""The simplified reviewer: one LLM call over the PR title, body and diff.

Per Execution_Roadmap_Weeks_0_to_18.md Phase 2: this is deliberately the
weakest baseline — no repository context, no graph, no verifier. Expect low
precision, hallucinated line numbers, generic advice. That's not a bug in
this module; it's the reason later stages (context retrieval, agents,
verification) exist. Kept as the "screen" tier once tiered routing exists.
"""

import json
import logging

from pydantic import BaseModel, Field, ValidationError

from revu.models import Finding, FindingCategory, RunResult, Severity
from revu.providers.llm import CompletionResult, complete

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a precise, conservative code reviewer. You are given a pull request's \
title, description, and unified diff. Identify concrete defects only: \
correctness bugs, security issues, performance regressions, missed test \
coverage, or clear convention violations visible in the diff itself.

Rules:
- Only comment on lines actually present in the diff.
- Do not restate what the diff does; only flag problems.
- Do not invent line numbers — use the line numbers as they appear in the diff.
- If you find nothing worth flagging, return an empty findings list. \
Silence is a valid, good answer.
- Reply with ONLY a JSON object of the exact shape:
  {"findings": [{"file_path": str, "line_start": int, "line_end": int, \
"category": one of ["correctness","security","performance","convention",\
"test_adequacy","maintainability"], "severity": one of \
["low","medium","high","critical"], "message": str, "confidence": float \
between 0 and 1}]}
No prose outside the JSON object.
"""

_RETRY_PROMPT = (
    "That was not valid JSON matching the required schema. "
    "Reply again with ONLY the corrected JSON object."
)


class _FindingPayload(BaseModel):
    file_path: str
    line_start: int
    line_end: int
    category: FindingCategory
    severity: Severity
    message: str
    confidence: float = Field(ge=0.0, le=1.0)


class _DiffOnlyOutput(BaseModel):
    findings: list[_FindingPayload] = Field(default_factory=list)


def _build_messages(pr_title: str, pr_body: str, diff: str) -> list[dict[str, str]]:
    user_content = (
        f"PR title: {pr_title}\n\nPR description:\n{pr_body or '(none)'}\n\nDiff:\n{diff}"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _parse(content: str) -> _DiffOnlyOutput | None:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    try:
        return _DiffOnlyOutput.model_validate(data)
    except ValidationError:
        return None


def _merge(first: CompletionResult, second: CompletionResult) -> CompletionResult:
    return CompletionResult(
        content=second.content,
        tokens_in=first.tokens_in + second.tokens_in,
        tokens_out=first.tokens_out + second.tokens_out,
        cost_usd=first.cost_usd + second.cost_usd,
        latency_ms=first.latency_ms + second.latency_ms,
    )


async def review_diff(*, pr_title: str, pr_body: str, diff: str, model: str) -> RunResult:
    messages = _build_messages(pr_title, pr_body, diff)

    result = await complete(model=model, messages=messages, response_format={"type": "json_object"})
    parsed = _parse(result.content)

    if parsed is None:
        logger.warning("diff_only: malformed JSON on first attempt; retrying with correction")
        messages = [
            *messages,
            {"role": "assistant", "content": result.content},
            {"role": "user", "content": _RETRY_PROMPT},
        ]
        retry_result = await complete(
            model=model, messages=messages, response_format={"type": "json_object"}
        )
        parsed = _parse(retry_result.content)
        result = _merge(result, retry_result)

    if parsed is None:
        logger.error(
            "diff_only: malformed JSON after retry, giving up; raw=%r", result.content[:500]
        )
        findings: list[Finding] = []
    else:
        findings = [
            Finding(
                file_path=f.file_path,
                line_start=f.line_start,
                line_end=f.line_end,
                category=f.category,
                severity=f.severity,
                message=f.message,
                evidence=[],
                confidence=f.confidence,
                agent_name="diff_only",
            )
            for f in parsed.findings
        ]

    return RunResult(
        findings=findings,
        context_bundle=None,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
    )
