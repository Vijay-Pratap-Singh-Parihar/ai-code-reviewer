"""Running review systems over benchmark instances.

- **Resumable.** Every finished run is appended to a JSONL store as it
  completes; a restarted benchmark skips what is already there, so a
  dropped connection or a crash never pays for the same PR twice.
- **Budgeted.** Runs stop before the recorded spend would pass
  `budget_usd`. Paid runs need an explicit budget; local models cost 0.
- **Same pipeline as production.** Systems call revu's engine with a
  `ModelEndpoint`, exactly as the worker does.

Model keys for benchmark runs are read from an environment variable named
in the config (`api_key_env`), never written into config or result files.
The product itself only takes keys from the AI Providers screen; this is an
offline research tool run by the person paying for the calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from revu.agents.diff_only import review_diff
from revu.models import Finding, RunResult
from revu.providers.llm import ModelEndpoint

from revu_bench.schema import BenchInstance, PredictedFinding, RunRecord

Agent = Literal["diff_only", "cross_file"]
NO_KEY = "not-needed"


class SystemConfig(BaseModel):
    """One system under test. `name` is how reports refer to it (B0, revu…)."""

    model_config = {"extra": "forbid"}

    name: str
    agent: Agent = "diff_only"
    model: str  # LiteLLM model string, e.g. "openai/qwen3.5:9b"
    api_base: str | None = None
    api_key_env: str | None = None  # name of the env var holding the key
    input_cost_per_mtok: float | None = None
    output_cost_per_mtok: float | None = None

    def endpoint(self) -> ModelEndpoint:
        key = os.environ.get(self.api_key_env, "") if self.api_key_env else NO_KEY
        if self.api_key_env and not key:
            raise RuntimeError(f"{self.name}: environment variable {self.api_key_env} is not set")
        return ModelEndpoint(
            model=self.model,
            api_key=key or NO_KEY,
            api_base=self.api_base,
            input_cost_per_mtok=self.input_cost_per_mtok,
            output_cost_per_mtok=self.output_cost_per_mtok,
        )


class BenchConfig(BaseModel):
    model_config = {"extra": "forbid"}

    systems: list[SystemConfig] = Field(min_length=1)


def load_config(path: Path) -> BenchConfig:
    return BenchConfig.model_validate_json(path.read_text(encoding="utf-8"))


class ResultStore:
    """Append-only JSONL of `RunRecord`s."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def records(self) -> list[RunRecord]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as handle:
            return [RunRecord.model_validate_json(line) for line in handle if line.strip()]

    def latest(self) -> dict[tuple[str, str, int], RunRecord]:
        """The last record for each (instance, config, repeat): a retried
        run supersedes its earlier failure."""
        return {(r.instance_id, r.config, r.repeat): r for r in self.records()}

    def append(self, record: RunRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")


def to_predicted(finding: Finding) -> PredictedFinding:
    return PredictedFinding(
        path=finding.file_path,
        line_start=finding.line_start,
        line_end=finding.line_end,
        message=finding.message,
        category=str(finding.category),
        severity=str(finding.severity),
        confidence=finding.confidence,
    )


Reviewer = Callable[[BenchInstance, SystemConfig], Awaitable[RunResult]]


async def engine_reviewer(instance: BenchInstance, system: SystemConfig) -> RunResult:
    if system.agent != "diff_only":
        # Cross-file needs a checkout at base_sha and a built index; that
        # lands with the repository-checkout stage of the harness.
        raise NotImplementedError("cross_file benchmark runs need a repository checkout")
    return await review_diff(
        pr_title=instance.pr_title,
        pr_body=instance.pr_body,
        diff=instance.diff,
        model=system.endpoint(),
    )


class BudgetExceeded(RuntimeError):
    pass


def is_hosted(system: SystemConfig) -> bool:
    """Calls a paid hosted API (no local `api_base`). Local servers such as
    Ollama are reached through an `api_base` on this machine or network."""
    return system.api_base is None


async def run_benchmark(
    instances: Sequence[BenchInstance],
    systems: Sequence[SystemConfig],
    store: ResultStore,
    *,
    repeats: int = 1,
    budget_usd: float = 0.0,
    retry_errors: bool = False,
    concurrency: int = 1,
    reviewer: Reviewer = engine_reviewer,
    on_record: Callable[[RunRecord], None] | None = None,
) -> list[RunRecord]:
    """Run every system on every instance `repeats` times, skipping runs
    already in `store`. Stops (raising `BudgetExceeded`) once the spend
    recorded in the store reaches `budget_usd`; 0 means free models only."""
    hosted = [s.name for s in systems if is_hosted(s)]
    if hosted and budget_usd <= 0:
        raise BudgetExceeded(
            f"{', '.join(hosted)} call a hosted (paid) API; set a budget to run them"
        )
    existing = store.latest()
    spent = sum(r.cost_usd for r in existing.values())
    todo = [
        (instance, system, repeat)
        for system in systems
        for repeat in range(repeats)
        for instance in instances
        if (key := (instance.id, system.name, repeat)) not in existing
        or (retry_errors and not existing[key].ok)
    ]
    finished: list[RunRecord] = []
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(instance: BenchInstance, system: SystemConfig, repeat: int) -> None:
        nonlocal spent
        async with semaphore:
            async with lock:
                if spent > budget_usd:
                    raise BudgetExceeded(
                        f"spent ${spent:.4f} of the ${budget_usd:.4f} budget; stopping"
                    )
            start = time.monotonic()
            try:
                result = await reviewer(instance, system)
                record = RunRecord(
                    instance_id=instance.id,
                    config=system.name,
                    repeat=repeat,
                    findings=[to_predicted(f) for f in result.findings],
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    cost_usd=result.cost_usd,
                    latency_ms=result.latency_ms or int((time.monotonic() - start) * 1000),
                )
            except NotImplementedError:
                raise
            except Exception as exc:  # a failed review is a result, not a crash
                record = RunRecord(
                    instance_id=instance.id,
                    config=system.name,
                    repeat=repeat,
                    latency_ms=int((time.monotonic() - start) * 1000),
                    error=f"{type(exc).__name__}: {str(exc)[:500]}",
                )
            async with lock:
                store.append(record)
                spent += record.cost_usd
                finished.append(record)
            if on_record is not None:
                on_record(record)

    await asyncio.gather(*(one(i, s, r) for i, s, r in todo))
    return finished


def dump_config_template(path: Path) -> None:
    """A starting config: a free local model and a paid hosted one."""
    template = BenchConfig(
        systems=[
            SystemConfig(
                name="B0-local-qwen",
                agent="diff_only",
                model="openai/qwen3.5:9b",
                api_base="http://127.0.0.1:11434/v1",
            ),
            SystemConfig(
                name="B0-groq-gpt-oss-120b",
                agent="diff_only",
                model="groq/openai/gpt-oss-120b",
                api_key_env="GROQ_API_KEY",
            ),
        ]
    )
    path.write_text(
        json.dumps(template.model_dump(exclude_none=True), indent=2) + "\n", encoding="utf-8"
    )
