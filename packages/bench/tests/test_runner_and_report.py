import json
from pathlib import Path

import pytest
from revu.models import EvidenceItem, Finding, FindingCategory, RunResult, Severity
from revu_bench.__main__ import main
from revu_bench.datasets import fingerprint, load_instances, make_split, save_instances, select
from revu_bench.report import write_report
from revu_bench.runner import (
    BudgetExceeded,
    ResultStore,
    SystemConfig,
    dump_config_template,
    load_config,
    run_benchmark,
)
from revu_bench.schema import BenchInstance, GroundTruthComment

LOCAL = SystemConfig(name="local", model="openai/m", api_base="http://127.0.0.1:11434/v1")
HOSTED = SystemConfig(name="hosted", model="groq/m", api_key_env="FAKE_KEY")


def instances(n: int = 6) -> list[BenchInstance]:
    return [
        BenchInstance(
            id=f"pr-{i}",
            repo="acme/x",
            language="python" if i % 3 else "go",
            diff=f"diff {i}",
            ground_truth=[
                GroundTruthComment(path="a.py", line_start=i, line_end=i, context_level="diff")
            ],
        )
        for i in range(1, n + 1)
    ]


def finding(line: int) -> Finding:
    return Finding(
        file_path="a.py",
        line_start=line,
        line_end=line,
        category=FindingCategory.CORRECTNESS,
        severity=Severity.HIGH,
        message="m",
        evidence=[EvidenceItem(file_path="a.py", line_start=line, line_end=line, reason="r")],
        confidence=0.8,
        agent_name="diff_only",
    )


def reviewer(hit: bool = True, cost: float = 0.0, fail_on: str | None = None):  # type: ignore[no-untyped-def]
    calls: list[str] = []

    async def review(instance: BenchInstance, system: SystemConfig) -> RunResult:
        calls.append(f"{system.name}:{instance.id}")
        if instance.id == fail_on:
            raise TimeoutError("model timed out")
        line = instance.ground_truth[0].line_start if hit else 999
        return RunResult(
            findings=[finding(line)], tokens_in=10, tokens_out=5, cost_usd=cost, latency_ms=50
        )

    review.calls = calls  # type: ignore[attr-defined]
    return review


def test_split_is_deterministic_stratified_and_complete() -> None:
    data = instances(30)
    first, second = make_split(data, dev_size=6), make_split(list(reversed(data)), dev_size=6)
    assert first == second
    dev = [i for i in data if first[i.id] == "dev"]
    assert len(dev) == 6
    assert {i.language for i in dev} == {"python", "go"}  # both languages represented
    assert len(select(data, first, "test")) == 24


def test_instances_round_trip_and_fingerprint_tracks_ground_truth(tmp_path: Path) -> None:
    data = instances(3)
    save_instances(tmp_path / "i.jsonl", data)
    loaded = load_instances(tmp_path / "i.jsonl")
    assert loaded == data
    changed = [*data[:2], data[2].model_copy(update={"ground_truth": []})]
    assert fingerprint(loaded) != fingerprint(changed)


async def test_runs_are_resumable_and_never_repeated(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / "results.jsonl")
    first = reviewer(fail_on="pr-2")

    await run_benchmark(instances(3), [LOCAL], store, reviewer=first)
    second = reviewer()
    await run_benchmark(instances(3), [LOCAL], store, reviewer=second)
    await run_benchmark(instances(3), [LOCAL], store, reviewer=second, retry_errors=True)

    assert len(first.calls) == 3
    assert second.calls == ["local:pr-2"]  # only the failed one, and only when asked
    latest = store.latest()
    assert all(r.ok for r in latest.values()) and len(latest) == 3


async def test_hosted_systems_need_a_budget_and_stop_when_it_is_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_KEY", "k")
    store = ResultStore(tmp_path / "results.jsonl")

    with pytest.raises(BudgetExceeded, match="set a budget"):
        await run_benchmark(instances(4), [HOSTED], store, reviewer=reviewer(cost=0.01))

    spending = reviewer(cost=0.01)
    with pytest.raises(BudgetExceeded):
        await run_benchmark(instances(4), [HOSTED], store, budget_usd=0.015, reviewer=spending)
    assert len(spending.calls) == 2  # the call that crossed the budget, then stop
    assert sum(r.cost_usd for r in store.records()) == pytest.approx(0.02)


def test_a_missing_key_variable_is_reported_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAKE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FAKE_KEY is not set"):
        HOSTED.endpoint()
    assert LOCAL.endpoint().api_key == "not-needed"


async def test_report_compares_systems_on_the_same_prs(tmp_path: Path) -> None:
    data = instances(12)
    store = ResultStore(tmp_path / "results.jsonl")
    weak = SystemConfig(name="weak", model="openai/w", api_base="http://127.0.0.1:1/v1")
    await run_benchmark(data, [LOCAL], store, reviewer=reviewer(hit=True))
    await run_benchmark(data, [weak], store, reviewer=reviewer(hit=False))

    summaries = write_report(
        tmp_path / "report.md",
        data,
        list(store.latest().values()),
        baseline="weak",
        title="Test",
        data_fingerprint=fingerprint(data),
    )
    text = (tmp_path / "report.md").read_text(encoding="utf-8")

    assert summaries["local"].f1 == 1.0 and summaries["weak"].f1 == 0.0
    assert "| local vs weak" not in text and "| System vs weak" in text
    assert "+1.000" in text and "large" in text
    assert (tmp_path / "report.csv").read_text(encoding="utf-8").startswith("config,")


def test_cli_split_and_config_template(tmp_path: Path) -> None:
    save_instances(tmp_path / "i.jsonl", instances(10))

    assert (
        main(
            [
                "split",
                "--instances",
                str(tmp_path / "i.jsonl"),
                "--dev-size",
                "3",
                "--out",
                str(tmp_path / "split.json"),
            ]
        )
        == 0
    )
    assert main(["config", "--out", str(tmp_path / "systems.json")]) == 0

    split = json.loads((tmp_path / "split.json").read_text(encoding="utf-8"))
    assert sum(v == "dev" for v in split["split"].values()) == 3
    systems = load_config(tmp_path / "systems.json").systems
    assert [s.name for s in systems] == ["B0-local-qwen", "B0-groq-gpt-oss-120b"]
    # The hosted system uses the provider configured in the UI, not a key in the shell.
    assert (systems[1].org, systems[1].provider) == ("Your organization", "Groq")
    assert systems[1].api_key_env is None and "api_key" not in json.dumps(split)


def test_config_template_never_contains_a_key(tmp_path: Path) -> None:
    dump_config_template(tmp_path / "c.json")
    assert "gsk_" not in (tmp_path / "c.json").read_text(encoding="utf-8")
