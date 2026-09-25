import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from db.branch_index import BranchIndex
from db.pull_request import AnalysisRun, AnalysisRunStatus, FindingRecord
from revu.agents.cross_file import review_cross_file
from revu.agents.diff_only import review_diff
from revu.index.store import load_graph
from revu.models import Finding
from revu.verify import verify_findings
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-sonnet-5"


async def _run_cross_file(
    *, run: AnalysisRun, session: AsyncSession, pr_title: str, pr_body: str, diff: str,
    repo_path: str, model: str,
) -> list[Finding]:
    """The `agent="cross_file"` path: load the graph the caller's already-`ready`
    branch index built (validated at request time by
    `api.services.analysis.create_analysis_run` — a missing/not-ready index is
    a 409 the caller sees immediately, not a job failure discovered later),
    then run every finding through Stage 8's verifier before persisting, since
    a tool-calling agent's self-cited evidence is exactly what that verifier
    was built to catch when fabricated (see IMPLEMENTATION_PLAN.md Stage 8's
    "wiring both cross_file and verify_findings into that job together").
    """
    if run.branch_index_id is None:
        raise RuntimeError("cross_file run has no branch_index_id set")

    branch_index = await session.get(BranchIndex, run.branch_index_id)
    if branch_index is None or branch_index.graph_ref is None:
        raise RuntimeError(f"branch index {run.branch_index_id} has no usable graph_ref")

    graph = load_graph(Path(branch_index.graph_ref))
    repo_root = Path(repo_path)

    result = await review_cross_file(
        pr_title=pr_title, pr_body=pr_body, diff=diff,
        repo_root=repo_root, graph=graph, model=model,
    )

    verification = verify_findings(result.findings, repo_root=repo_root)
    run.config_snapshot = {**run.config_snapshot, "verification": asdict(verification.report)}
    run.tokens_in = result.tokens_in
    run.tokens_out = result.tokens_out
    run.cost_usd = result.cost_usd
    run.latency_ms = result.latency_ms
    return verification.findings


async def analyze_pr(
    ctx: dict[str, Any],
    run_id: str,
    pr_title: str,
    pr_body: str,
    diff: str,
    agent: str = "diff_only",
    repo_path: str | None = None,
) -> None:
    """Runs the selected reviewer for one analysis run and persists the
    result. `diff`/`repo_path` are passed as job arguments rather than
    fetched from GitHub or read off the run row — see `AnalysisRequest`'s
    docstring for why. `agent` mirrors `AnalysisRequest.agent`: the caller's
    own cost/depth choice, not something this job escalates on its own.
    """
    session_factory = ctx["db_session_factory"]

    async with session_factory() as session:
        run = await session.get(AnalysisRun, uuid.UUID(run_id))
        if run is None:
            logger.error("analyze_pr: run %s no longer exists", run_id)
            return

        run.status = AnalysisRunStatus.RUNNING
        run.started_at = datetime.now(UTC)
        await session.commit()

        model = str(run.config_snapshot.get("model", _DEFAULT_MODEL))

        try:
            if agent == "cross_file":
                if not repo_path:
                    raise RuntimeError("agent='cross_file' requires repo_path")
                findings = await _run_cross_file(
                    run=run, session=session, pr_title=pr_title, pr_body=pr_body,
                    diff=diff, repo_path=repo_path, model=model,
                )
            else:
                result = await review_diff(
                    pr_title=pr_title, pr_body=pr_body, diff=diff, model=model
                )
                run.tokens_in = result.tokens_in
                run.tokens_out = result.tokens_out
                run.cost_usd = result.cost_usd
                run.latency_ms = result.latency_ms
                findings = result.findings
        except Exception as exc:
            logger.exception("analyze_pr: run %s failed", run_id)
            run.status = AnalysisRunStatus.FAILED
            run.error = str(exc)
            run.finished_at = datetime.now(UTC)
            await session.commit()
            return

        for finding in findings:
            session.add(
                FindingRecord(
                    run_id=run.id,
                    file_path=finding.file_path,
                    line_start=finding.line_start,
                    line_end=finding.line_end,
                    category=finding.category,
                    severity=finding.severity,
                    message=finding.message,
                    evidence_json=[e.model_dump() for e in finding.evidence],
                    confidence=finding.confidence,
                    agent_name=finding.agent_name,
                )
            )

        run.status = AnalysisRunStatus.SUCCEEDED
        run.finished_at = datetime.now(UTC)
        await session.commit()
