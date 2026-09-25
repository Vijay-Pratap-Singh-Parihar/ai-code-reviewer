"""Tools the cross-file agent (Stage 7) can call. Each tool is a plain,
stateless, structured-data function — Pydantic in, Pydantic out — dispatched
by name from an LLM tool-call request via `call_tool`.

`TOOL_SCHEMAS` is the OpenAI-style tool-calling schema LiteLLM expects
(provider-agnostic — LiteLLM translates this shape for whichever provider
`revu.providers.llm.complete` is pointed at).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import rustworkx as rx

from revu.agents.tools.find_callers import FindCallersResult, find_callers
from revu.agents.tools.find_definition import DefinitionResult, find_definition
from revu.agents.tools.graph_query import GraphQueryResult, graph_query
from revu.agents.tools.read_file import ReadFileResult, read_file

logger = logging.getLogger(__name__)

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file's content from the repository, optionally restricted to a "
                "line range."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relative to the repository root.",
                    },
                    "line_start": {
                        "type": "integer",
                        "description": "1-indexed inclusive start line. Omit for line 1.",
                    },
                    "line_end": {
                        "type": "integer",
                        "description": "1-indexed inclusive end line. Omit for the last line.",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graph_query",
            "description": (
                "Look up a symbol's definition location and every import/call edge "
                "directly touching it, by its qualified name "
                "(e.g. 'package.module.ClassName.method_name')."
            ),
            "parameters": {
                "type": "object",
                "properties": {"qualified_name": {"type": "string"}},
                "required": ["qualified_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_definition",
            "description": "Find where a symbol is defined, by its qualified name.",
            "parameters": {
                "type": "object",
                "properties": {"qualified_name": {"type": "string"}},
                "required": ["qualified_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_callers",
            "description": "Find who calls a symbol, up to k hops away, nearest first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "qualified_name": {"type": "string"},
                    "k": {
                        "type": "integer",
                        "description": "Max hops to search. Defaults to 1 (direct callers).",
                    },
                },
                "required": ["qualified_name"],
            },
        },
    },
]


class ToolContext:
    """The parts of a tool call the LLM shouldn't have to (and can't
    meaningfully) supply itself: a repo checkout to read files from, and the
    graph to query. Bound once per agent run, passed into every `call_tool`.
    """

    def __init__(self, repo_root: Path, graph: rx.PyDiGraph) -> None:
        self.repo_root = repo_root
        self.graph = graph


def call_tool(name: str, arguments: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Dispatch one LLM-requested tool call by name. Returns a plain
    JSON-able dict. Never raises — `name` and `arguments` both come from
    model output, which can be wrong (unknown tool name, missing required
    argument, wrong type); any of that becomes a structured `{"error": ...}`
    result the agent sees in its next turn, not a crashed run.
    """
    result: ReadFileResult | GraphQueryResult | DefinitionResult | FindCallersResult
    try:
        if name == "read_file":
            result = read_file(
                ctx.repo_root,
                arguments["file_path"],
                line_start=arguments.get("line_start"),
                line_end=arguments.get("line_end"),
            )
        elif name == "graph_query":
            result = graph_query(ctx.graph, arguments["qualified_name"])
        elif name == "find_definition":
            result = find_definition(ctx.graph, arguments["qualified_name"])
        elif name == "find_callers":
            result = find_callers(
                ctx.graph, arguments["qualified_name"], k=int(arguments.get("k", 1))
            )
        else:
            return {"error": f"unknown tool: {name}"}
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("tool call %s(%r) failed: %s", name, arguments, exc)
        return {"error": f"invalid arguments for {name}: {exc}"}

    return result.model_dump()


__all__ = [
    "TOOL_SCHEMAS",
    "ToolContext",
    "call_tool",
    "find_callers",
    "find_definition",
    "graph_query",
    "read_file",
]
