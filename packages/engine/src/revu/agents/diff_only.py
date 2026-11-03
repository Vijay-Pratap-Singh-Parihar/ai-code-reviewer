"""The simplified reviewer: one LLM call over the PR title, body and diff.

Per Execution_Roadmap_Weeks_0_to_18.md Phase 2: this is deliberately the
weakest baseline — no repository context, no graph, no verifier. Expect low
precision, hallucinated line numbers, generic advice. That's not a bug in
this module; it's the reason later stages (context retrieval, agents,
verification) exist. Kept as the "screen" tier once tiered routing exists.
"""

import asyncio
import json
import logging

from pydantic import BaseModel, Field, ValidationError

from revu.context.chunks import DEFAULT_MAX_CHARS, split_for_review
from revu.context.noise import drop_noise
from revu.models import Finding, FindingCategory, RunResult, Severity
from revu.providers.llm import CompletionResult, ModelEndpoint, complete, extract_json

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


def _build_messages(
    pr_title: str, pr_body: str, diff: str, part: tuple[int, int] = (1, 1)
) -> list[dict[str, str]]:
    index, total = part
    heading = (
        "Diff"
        if total == 1
        else f"Diff (part {index} of {total}; the other files are reviewed separately)"
    )
    user_content = (
        f"PR title: {pr_title}\n\nPR description:\n{pr_body or '(none)'}\n\n{heading}:\n{diff}"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _parse(content: str) -> _DiffOnlyOutput | None:
    try:
        data = extract_json(content)
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


async def review_diff(
    *,
    pr_title: str,
    pr_body: str,
    diff: str,
    model: str | ModelEndpoint,
    max_part_chars: int = DEFAULT_MAX_CHARS,
    concurrency: int = 2,
) -> RunResult:
    """Review `diff` without lockfiles and generated files, in parts when it
    is large (`revu.context.chunks`). Parts are reviewed concurrently and
    their findings concatenated; tokens, cost and model time are summed."""
    filtered = drop_noise(diff)
    parts = split_for_review(filtered.diff, max_part_chars)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(index: int, part: str) -> tuple[list[Finding], CompletionResult, str | None]:
        async with semaphore:
            return await _review_part(
                pr_title, pr_body, part, model, part=(index, len(parts))
            )

    results = await asyncio.gather(
        *(one(i, p) for i, p in enumerate(parts, start=1)), return_exceptions=True
    )
    for r in results:
        if isinstance(r, BaseException) and not isinstance(r, Exception):
            raise r  # cancellation, not a failed call
    failures = [r for r in results if isinstance(r, Exception)]
    if failures and len(failures) == len(results):
        raise failures[0]
    # A part that failed (a timeout, say) loses only its own findings; the
    # run says how many parts that was rather than failing outright.
    outcomes = [r for r in results if not isinstance(r, BaseException)]
    findings = [f for part_findings, _, _ in outcomes for f in part_findings]
    reasons = [reason for _, _, reason in outcomes if reason]
    if failures:
        logger.warning("diff_only: %d of %d parts failed: %r", len(failures), len(parts), failures)
        reasons.insert(0, f"parts_failed ({len(failures)} of {len(parts)})")
    return RunResult(
        findings=findings,
        context_bundle=None,
        tokens_in=sum(r.tokens_in for _, r, _ in outcomes),
        tokens_out=sum(r.tokens_out for _, r, _ in outcomes),
        cost_usd=sum(r.cost_usd for _, r, _ in outcomes),
        latency_ms=sum(r.latency_ms for _, r, _ in outcomes),
        stopped_reason=reasons[0] if reasons else None,
        skipped_files=filtered.skipped,
        parts=len(parts),
    )


async def _review_part(
    pr_title: str,
    pr_body: str,
    diff: str,
    model: str | ModelEndpoint,
    *,
    part: tuple[int, int],
) -> tuple[list[Finding], CompletionResult, str | None]:
    """One model call over one part (plus a JSON repair if needed): its
    findings, usage, and why it produced none if it couldn't answer."""
    messages = _build_messages(pr_title, pr_body, diff, part)

    result = await complete(model=model, messages=messages, response_format={"type": "json_object"})
    parsed = _parse(result.content)

    if parsed is None and result.truncated:
        # Asking a model that ran out of room to repeat itself in full
        # only runs out of room again.
        logger.warning("diff_only: answer cut off at the output token limit")
        return [], result, "output_token_limit"

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
        return [], result, "final_answer_malformed_after_retry"

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
    return findings, result, None
