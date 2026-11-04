"""Worker jobs log with the request that queued them, and get real limits."""

from typing import Any

import pytest
from observability import current_fields
from worker.job_context import traced
from worker.jobs.analyze import REVIEW_JOB_TIMEOUT_S, REVIEW_MAX_TRIES
from worker.settings import WorkerSettings


async def test_a_job_logs_with_the_request_id_job_and_its_ids() -> None:
    seen: dict[str, str] = {}

    async def analyze_pr(ctx: dict[str, Any], run_id: str, org_id: str) -> str:
        seen.update(current_fields())
        return "done"

    job = traced(analyze_pr)
    result = await job({"job_id": "j1", "job_try": 2}, "run-1", "org-1", request_id="req-9")

    assert result == "done"  # request_id is the wrapper's, never the job's
    assert seen == {
        "request_id": "req-9",
        "job": "analyze_pr",
        "job_id": "j1",
        "attempt": "2",
        "run_id": "run-1",
        "org_id": "org-1",
    }
    assert current_fields() == {}
    assert job.__qualname__.endswith("analyze_pr")  # the name arq queues it under


async def test_a_job_queued_without_a_request_id_still_runs() -> None:
    async def ping(ctx: dict[str, Any]) -> str:
        return "pong"

    assert await traced(ping)({}) == "pong"


async def test_a_failing_job_still_raises() -> None:
    async def broken(ctx: dict[str, Any]) -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await traced(broken)({})


def test_reviews_get_a_time_limit_long_enough_for_a_local_model() -> None:
    by_name = {f.name: f for f in WorkerSettings.functions}

    for name in ("analyze_pr", "review_github_pr"):
        assert by_name[name].timeout_s == REVIEW_JOB_TIMEOUT_S
        assert by_name[name].max_tries == REVIEW_MAX_TRIES
    assert REVIEW_JOB_TIMEOUT_S >= 30 * 60  # arq's own default is 5 minutes
    assert {c.name for c in WorkerSettings.cron_jobs} >= {
        "cron:purge_expired_data",
        "cron:fail_stuck_runs",
    }
