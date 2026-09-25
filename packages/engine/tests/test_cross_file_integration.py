"""Stage 7's hand-verified acceptance check, in the same spirit as Stage 4's
"10 hand-verified call edges" and Stage 6's `test_integration.py`: build a
*real* index of this repository's own working tree, use the *real* tools
against it, and drive the LangGraph loop with a scripted "smart" fake LLM
that behaves like a genuine tool-calling agent (requests find_callers, reads
the caller it found, then cites it as evidence) — rather than a real model
call, which would cost real money and isn't deterministic to assert against.

This proves the wiring end to end: real Pydantic tool results serialised
into tool-role messages, the LangGraph loop correctly turning multiple
rounds of "model wants a tool" into "model has an answer", and the final
answer's evidence actually tracing back to what a tool call found — not
just that mocked pieces individually work.

Same real cross-file relationship Stage 6's integration test hand-verified
by reading the source directly: `api.services.repositories
.get_or_create_repository` has exactly one resolved caller in this
repository, `api.services.branch_index.trigger_index_build`.
"""

import json
from pathlib import Path

import pytest
from revu.agents import cross_file
from revu.index.graph import IndexResult
from revu.providers.llm import CompletionResult, ToolCall

CHANGED_FILE = "apps/api/src/api/services/repositories.py"
CALLER_FILE = "apps/api/src/api/services/branch_index.py"

_CHANGED_LINE = (
    "+    repo = await session.scalar("
    "select(Repository).where(Repository.full_name == full_name.strip()))"
)

DIFF_TEXT = f"""\
diff --git a/{CHANGED_FILE} b/{CHANGED_FILE}
--- a/{CHANGED_FILE}
+++ b/{CHANGED_FILE}
@@ -32,4 +32,4 @@ async def get_or_create_repository(
 ) -> Repository:
-    repo = await session.scalar(select(Repository).where(Repository.full_name == full_name))
{_CHANGED_LINE}
     if repo is not None:
         if repo.org_id != org_id:
"""


def _finding_json(evidence_file: str) -> str:
    return json.dumps(
        {
            "findings": [
                {
                    "file_path": CHANGED_FILE,
                    "line_start": 32,
                    "line_end": 35,
                    "category": "correctness",
                    "severity": "medium",
                    "message": (
                        "full_name is now stripped before lookup, but callers may still pass "
                        "unstripped names expecting exact-match semantics"
                    ),
                    "confidence": 0.7,
                    "evidence": [
                        {
                            "file_path": evidence_file,
                            "line_start": 1,
                            "line_end": 1,
                            "reason": "resolved caller of the changed function",
                        }
                    ],
                }
            ]
        }
    )


async def test_agent_uses_find_callers_then_read_file_then_cites_real_evidence(
    real_repo_root: Path, real_repo_index: IndexResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    rounds: list[str] = []

    async def scripted_agent(
        *, model: str, messages: list[dict[str, object]], tools: object = None, **kwargs: object
    ) -> CompletionResult:
        last = messages[-1]

        if last["role"] == "user":
            # Round 1: investigate who calls the changed function.
            rounds.append("find_callers")
            args = {
                "qualified_name": "api.services.repositories.get_or_create_repository",
                "k": 2,
            }
            return CompletionResult(
                content="",
                tokens_in=100,
                tokens_out=20,
                cost_usd=0.001,
                latency_ms=10,
                tool_calls=[
                    ToolCall(
                        id="call_1", name="find_callers", arguments=args,
                        raw_arguments=json.dumps(args),
                    )
                ],
            )

        assert last["role"] == "tool"
        tool_result = json.loads(last["content"])

        if rounds == ["find_callers"]:
            # The real graph must have found the real caller.
            caller_names = [c["qualified_name"] for c in tool_result["callers"]]
            assert "api.services.branch_index.trigger_index_build" in caller_names
            caller = next(
                c for c in tool_result["callers"]
                if c["qualified_name"] == "api.services.branch_index.trigger_index_build"
            )
            assert caller["file_path"] == CALLER_FILE

            # Round 2: read the real caller file to check it against the new behavior.
            rounds.append("read_file")
            args = {
                "file_path": CALLER_FILE,
                "line_start": caller["line_start"],
                "line_end": caller["line_end"],
            }
            return CompletionResult(
                content="",
                tokens_in=150,
                tokens_out=20,
                cost_usd=0.002,
                latency_ms=15,
                tool_calls=[
                    ToolCall(
                        id="call_2", name="read_file", arguments=args,
                        raw_arguments=json.dumps(args),
                    )
                ],
            )

        assert rounds == ["find_callers", "read_file"]
        # The tool must have actually read real file content.
        assert tool_result["found"] is True
        assert "get_or_create_repository" in tool_result["content"]

        # Round 3: final answer, citing the real caller file as evidence.
        rounds.append("final_answer")
        return CompletionResult(
            content=_finding_json(CALLER_FILE), tokens_in=200, tokens_out=80,
            cost_usd=0.003, latency_ms=20,
        )

    monkeypatch.setattr(cross_file, "complete", scripted_agent)

    result = await cross_file.review_cross_file(
        pr_title="Strip full_name before repository lookup",
        pr_body="Trims whitespace before comparing repository names.",
        diff=DIFF_TEXT,
        repo_root=real_repo_root,
        graph=real_repo_index.graph,
        model="fake-model",
    )

    assert rounds == ["find_callers", "read_file", "final_answer"]
    assert result.stopped_reason is None
    assert len(result.findings) == 1

    finding = result.findings[0]
    assert finding.agent_name == "cross_file"
    assert finding.evidence[0].file_path == CALLER_FILE
    assert finding.evidence[0].reason == "resolved caller of the changed function"

    # Token/cost accounting sums every round, not just the last one.
    assert result.tokens_in == 100 + 150 + 200
    assert result.tokens_out == 20 + 20 + 80
    assert result.cost_usd == pytest.approx(0.001 + 0.002 + 0.003)
