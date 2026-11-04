"""Log context for worker jobs.

`traced` wraps a job so every line it logs carries the id of the request that
queued it (`api.core.queue.enqueue` passes it as `request_id`), the job's
name, arq's job id and attempt, and the run, organisation or repository it
is about, taken from the job's own arguments. It also logs how each attempt
ended and how long it took.
"""

from __future__ import annotations

import functools
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

from arq.typing import WorkerCoroutine
from observability import bound

logger = logging.getLogger("worker.jobs")

# Job arguments worth a field on every line the job logs.
_ID_ARGS = ("run_id", "org_id", "repo_id", "installation_id", "branch_index_id")

Job = Callable[..., Awaitable[Any]]


def traced(job: Job) -> WorkerCoroutine:
    signature = inspect.signature(job)

    @functools.wraps(job)
    async def run(
        ctx: dict[str, Any], *args: Any, request_id: str | None = None, **kwargs: Any
    ) -> Any:
        try:
            bound_args = signature.bind_partial(ctx, *args, **kwargs).arguments
        except TypeError:
            bound_args = {}
        ids = {name: bound_args[name] for name in _ID_ARGS if bound_args.get(name)}
        with bound(
            request_id=request_id,
            job=job.__name__,
            job_id=ctx.get("job_id"),
            attempt=ctx.get("job_try"),
            **ids,
        ):
            start = time.perf_counter()
            outcome = "failed"
            try:
                result = await job(ctx, *args, **kwargs)
                outcome = "done"
                return result
            except BaseException as exc:
                outcome = type(exc).__name__
                raise
            finally:
                logger.info(
                    "job %s %s",
                    job.__name__,
                    outcome,
                    extra={"duration_ms": round((time.perf_counter() - start) * 1000)},
                )

    return cast(WorkerCoroutine, run)
