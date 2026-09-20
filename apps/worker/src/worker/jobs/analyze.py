import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from db.pull_request import AnalysisRun, AnalysisRunStatus, FindingRecord
from revu.agents.diff_only import review_diff

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-sonnet-5"


async def analyze_pr(
    ctx: dict[str, Any], run_id: str, pr_title: str, pr_body: str, diff: str
) -> None:
    """Runs the Stage 3 simplified reviewer for one analysis run and persists
    the result. `diff` is passed as a job argument rather than fetched from
    GitHub or read off the run row — see AnalysisRequest's docstring for why.
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
            result = await review_diff(pr_title=pr_title, pr_body=pr_body, diff=diff, model=model)
        except Exception as exc:
            logger.exception("analyze_pr: run %s failed", run_id)
            run.status = AnalysisRunStatus.FAILED
            run.error = str(exc)
            run.finished_at = datetime.now(UTC)
            await session.commit()
            return

        for finding in result.findings:
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
        run.tokens_in = result.tokens_in
        run.tokens_out = result.tokens_out
        run.cost_usd = result.cost_usd
        run.latency_ms = result.latency_ms
        run.finished_at = datetime.now(UTC)
        await session.commit()
