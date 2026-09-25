"""The cross-file consistency agent (Stage 7).

Seeded with Stage 6's pre-computed context bundle rather than exploring from
zero: per Product_Architecture_FullStack.md's own literature review, pure
agentic exploration is high-precision/low-recall and prone to "exploration
drift" - handing the agent likely-relevant context upfront and reserving
tools for targeted follow-up is the documented mitigation this project
chose. Tools (`revu.agents.tools`) let it verify and go beyond that seed:
checking a caller the bundle only summarised, or one more hop than the
bundle's k-value covered.

Orchestrated with LangGraph (a user choice - a manual tool-calling loop
would cost the same in API terms, but LangGraph is the base later agents in
this layer can share without a rewrite). LangGraph is used purely for
graph/state orchestration; the actual model calls still go through
`revu.providers.llm.complete`, not a LangChain chat-model wrapper.
"""

from __future__ import annotations

import json
import logging
import operator
from pathlib import Path
from typing import Annotated, Any, TypedDict

import rustworkx as rx
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field, ValidationError

from revu.agents.tools import TOOL_SCHEMAS, ToolContext, call_tool
from revu.context import RetrievalConfig, build_context_bundle
from revu.models import ContextBundle, EvidenceItem, Finding, FindingCategory, RunResult, Severity
from revu.providers.llm import complete

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8

_SYSTEM_PROMPT = """\
You are a Principal Software Architect performing a cross-file consistency \
review on a pull request before it merges. Your standard is production-grade: \
is this change safe to merge and deploy, or does it introduce risk that isn't \
visible from the diff alone? Unlike a reviewer who only sees the diff text, \
you have tools to inspect the actual repository: read any file, look up a \
symbol's definition and its edges, and find who calls a symbol. You are also \
given some pre-fetched context the repository's code graph already \
identified as likely relevant - use it as your starting point, and use tools \
for anything beyond it (verifying a caller in full, checking one more hop, \
looking at a symbol the pre-fetched context didn't include).

Your job is narrow and specific: find defects that are only visible by \
looking *beyond* the diff - a changed function whose callers weren't \
updated to match, a renamed or removed symbol still referenced elsewhere, a \
behavior change that breaks an assumption another file relies on, an API \
contract violated somewhere downstream. Do not report issues that are fully \
visible from the diff text alone with no cross-file investigation - that's \
a different reviewer's job, and duplicating it here isn't useful. Your \
judgment should reflect what a principal engineer would actually block a PR \
for: genuine risk to production, not nitpicks.

For every function, method, or class the diff changes: call find_callers to \
see who depends on it, then read_file on any caller you're unsure about to \
check it against the new behavior. Use graph_query or find_definition to \
inspect related symbols you don't already know. Investigate as thoroughly as \
the change warrants - use as many tool calls as you genuinely need to reach \
a confident, well-evidenced verdict.

Every finding you report must cite the specific file(s) and line(s) your \
tool calls (or the pre-fetched context) actually showed you. If you didn't \
look something up, you can't cite it as evidence. If your investigation \
finds nothing wrong, say so with an empty findings list - that's a good, \
valid answer.

Reply with ONLY a JSON object of the exact shape:
{"findings": [{"file_path": str, "line_start": int, "line_end": int, \
"category": one of ["correctness","security","performance","convention",\
"test_adequacy","maintainability"], "severity": one of \
["low","medium","high","critical"], "message": str, "confidence": float \
between 0 and 1, "evidence": [{"file_path": str, "line_start": int, \
"line_end": int, "reason": str}]}]}
No prose outside the JSON object.
"""

_RETRY_PROMPT = (
    "That was not valid JSON matching the required schema. "
    "Reply again with ONLY the corrected JSON object."
)


class _EvidencePayload(BaseModel):
    file_path: str
    line_start: int
    line_end: int
    reason: str


class _FindingPayload(BaseModel):
    file_path: str
    line_start: int
    line_end: int
    category: FindingCategory
    severity: Severity
    message: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[_EvidencePayload] = Field(default_factory=list)


class _CrossFileOutput(BaseModel):
    findings: list[_FindingPayload] = Field(default_factory=list)


def _parse(content: str) -> _CrossFileOutput | None:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    try:
        return _CrossFileOutput.model_validate(data)
    except ValidationError:
        return None


def _format_context_bundle(bundle: ContextBundle) -> str:
    if not bundle.items:
        return "(no related context retrieved)"
    parts = [
        f"--- {item.file_path}:{item.line_start}-{item.line_end} "
        f"({item.retrieval_reason}) ---\n{item.content}"
        for item in bundle.items
    ]
    return "\n\n".join(parts)


def _build_initial_messages(
    pr_title: str, pr_body: str, diff: str, context_bundle: ContextBundle
) -> list[dict[str, Any]]:
    user_content = (
        f"PR title: {pr_title}\n\nPR description:\n{pr_body or '(none)'}\n\nDiff:\n{diff}\n\n"
        "Pre-fetched related context (retrieved via the code graph; use tools for "
        f"anything beyond this):\n{_format_context_bundle(context_bundle)}"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


class _AgentState(TypedDict):
    messages: Annotated[list[dict[str, Any]], operator.add]
    rounds: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int


async def _agent_step(state: _AgentState, *, model: str) -> dict[str, Any]:
    result = await complete(model=model, messages=state["messages"], tools=TOOL_SCHEMAS)

    new_message: dict[str, Any] = {"role": "assistant", "content": result.content}
    if result.tool_calls:
        new_message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.raw_arguments},
            }
            for call in result.tool_calls
        ]

    return {
        "messages": [new_message],
        "rounds": state["rounds"] + 1,
        "tokens_in": state["tokens_in"] + result.tokens_in,
        "tokens_out": state["tokens_out"] + result.tokens_out,
        "cost_usd": state["cost_usd"] + result.cost_usd,
        "latency_ms": state["latency_ms"] + result.latency_ms,
    }


async def _tools_step(state: _AgentState, *, ctx: ToolContext) -> dict[str, Any]:
    last_message = state["messages"][-1]
    tool_calls = last_message.get("tool_calls") or []

    new_messages = []
    for call in tool_calls:
        try:
            arguments = json.loads(call["function"]["arguments"] or "{}")
            if not isinstance(arguments, dict):
                arguments = {}
        except json.JSONDecodeError:
            arguments = {}
        result = call_tool(call["function"]["name"], arguments, ctx)
        new_messages.append(
            {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)}
        )

    return {"messages": new_messages}


def _route_after_agent(state: _AgentState, *, max_rounds: int) -> str:
    last_message = state["messages"][-1]
    if last_message.get("tool_calls"):
        return "stopped" if state["rounds"] >= max_rounds else "tools"
    return "done"


def _build_graph(model: str, ctx: ToolContext, max_rounds: int) -> Any:
    async def agent_node(state: _AgentState) -> dict[str, Any]:
        return await _agent_step(state, model=model)

    async def tools_node(state: _AgentState) -> dict[str, Any]:
        return await _tools_step(state, ctx=ctx)

    def route(state: _AgentState) -> str:
        return _route_after_agent(state, max_rounds=max_rounds)

    graph: StateGraph[_AgentState] = StateGraph(_AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", "done": END, "stopped": END})
    graph.add_edge("tools", "agent")
    return graph.compile()


async def review_cross_file(
    *,
    pr_title: str,
    pr_body: str,
    diff: str,
    repo_root: Path,
    graph: rx.PyDiGraph,
    model: str,
    context_config: RetrievalConfig | None = None,
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> RunResult:
    """Run the cross-file agent over `diff` against `graph` (a Stage 4/5
    indexed graph) and `repo_root` (a working tree matching it).

    Zero LLM calls happen while building the context bundle (Stage 6 is
    graph-only) - every token counted comes from the agent's own
    tool-calling loop and, if needed, its one JSON-repair retry.
    """
    context_bundle = build_context_bundle(diff, graph, repo_root, config=context_config)
    messages = _build_initial_messages(pr_title, pr_body, diff, context_bundle)

    ctx = ToolContext(repo_root, graph)
    compiled = _build_graph(model, ctx, max_rounds)

    initial_state: _AgentState = {
        "messages": messages,
        "rounds": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        "latency_ms": 0,
    }
    final_state: _AgentState = await compiled.ainvoke(initial_state)

    tokens_in = final_state["tokens_in"]
    tokens_out = final_state["tokens_out"]
    cost_usd = final_state["cost_usd"]
    latency_ms = final_state["latency_ms"]
    last_message = final_state["messages"][-1]

    if last_message.get("tool_calls"):
        logger.warning("cross_file: hit max_rounds (%d) while still investigating", max_rounds)
        return RunResult(
            findings=[],
            context_bundle=context_bundle,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            stopped_reason=f"max_tool_rounds_reached ({max_rounds})",
        )

    content = last_message.get("content") or ""
    parsed = _parse(content)

    if parsed is None:
        logger.warning("cross_file: malformed final JSON; retrying with correction")
        retry_messages = [*final_state["messages"], {"role": "user", "content": _RETRY_PROMPT}]
        retry_result = await complete(model=model, messages=retry_messages)
        tokens_in += retry_result.tokens_in
        tokens_out += retry_result.tokens_out
        cost_usd += retry_result.cost_usd
        latency_ms += retry_result.latency_ms
        parsed = _parse(retry_result.content)

    if parsed is None:
        logger.error("cross_file: malformed JSON after retry, giving up; raw=%r", content[:500])
        return RunResult(
            findings=[],
            context_bundle=context_bundle,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            stopped_reason="final_answer_malformed_after_retry",
        )

    findings = [
        Finding(
            file_path=f.file_path,
            line_start=f.line_start,
            line_end=f.line_end,
            category=f.category,
            severity=f.severity,
            message=f.message,
            evidence=[EvidenceItem(**e.model_dump()) for e in f.evidence],
            confidence=f.confidence,
            agent_name="cross_file",
        )
        for f in parsed.findings
    ]

    return RunResult(
        findings=findings,
        context_bundle=context_bundle,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )
