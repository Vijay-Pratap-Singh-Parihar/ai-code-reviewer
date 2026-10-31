"""Command line: `uv run python -m revu_bench <command> ...`

prepare-aacr  download AACR-Bench (pinned), fetch each PR's diff, write instances
split    make and save the dev/test split (commit the output file)
config   write a starting systems config
run      run systems over a split, resumably, within a budget
report   score results and write the markdown + CSV report
judge    ask the judge model about plausible matches, caching its verdicts
label    export a blind sample of judged pairs for a person to label
agreement  Cohen's kappa between the judge and the person's labels
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from revu_bench.adapters import aacr
from revu_bench.datasets import (
    fingerprint,
    load_instances,
    load_split,
    make_split,
    save_instances,
    save_split,
    select,
)
from revu_bench.judge import (
    VerdictCache,
    cached_judge,
    candidate_pairs,
    cohens_kappa,
    export_for_labelling,
    judge_pairs,
    llm_ask,
    read_labels,
)
from revu_bench.providers import ProviderLookupError, resolve_ui_providers
from revu_bench.report import write_report
from revu_bench.runner import (
    BudgetExceeded,
    ResultStore,
    SystemConfig,
    dump_config_template,
    is_hosted,
    load_config,
    run_benchmark,
)
from revu_bench.schema import RunRecord


def _split_instances(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    instances = load_instances(Path(args.instances))
    if getattr(args, "split", None):
        split, recorded = load_split(Path(args.split))
        current = fingerprint(instances)
        if recorded != current:
            sys.exit(f"the split was made for data {recorded}, but these instances are {current}")
        instances = select(instances, split, args.which)
    return instances


def cmd_prepare_aacr(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir)
    raw = aacr.download_release(data_dir / "positive_samples.json")
    records = json.loads(raw.read_text(encoding="utf-8"))
    fetcher = aacr.GitHubFetcher(data_dir / "cache", token=aacr.github_token())
    failed: list[str] = []

    def report_failure(ref: aacr.PullRequestRef, exc: Exception) -> None:
        failed.append(ref.instance_id)
        print(f"skipped {ref.instance_id} ({ref.repo}#{ref.number}): {exc}", file=sys.stderr)

    instances = aacr.convert(records, fetcher, on_error=report_failure)
    out = Path(args.out)
    save_instances(out, instances)
    comments = sum(len(i.ground_truth) for i in instances)
    print(f"{len(instances)} instances, {comments} reference comments -> {out}")
    print(f"data fingerprint {fingerprint(instances)}")
    if failed:
        print(f"{len(failed)} pull requests couldn't be fetched: {', '.join(failed)}")
    return 0


def cmd_split(args: argparse.Namespace) -> int:
    instances = load_instances(Path(args.instances))
    split = make_split(instances, dev_size=args.dev_size, seed=args.seed)
    save_split(Path(args.out), split, data_fingerprint=fingerprint(instances), seed=args.seed)
    dev = sum(1 for v in split.values() if v == "dev")
    print(f"{dev} dev / {len(split) - dev} test -> {args.out}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    dump_config_template(Path(args.out))
    print(f"wrote {args.out}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    instances = _split_instances(args)
    systems = load_config(Path(args.config)).systems
    if args.systems:
        wanted = set(args.systems.split(","))
        systems = [s for s in systems if s.name in wanted]

    def progress(record: RunRecord) -> None:
        status = "ok" if record.ok else f"error: {record.error}"
        print(
            f"{record.config} #{record.repeat} {record.instance_id}: "
            f"{len(record.findings)} findings, ${record.cost_usd:.4f}, {status}"
        )

    try:
        endpoints = asyncio.run(resolve_ui_providers(systems))
    except ProviderLookupError as exc:
        sys.exit(str(exc))
    for system in systems:
        if system.name in endpoints:
            system.use_endpoint(endpoints[system.name])

    try:
        done = asyncio.run(
            run_benchmark(
                instances,
                systems,
                ResultStore(Path(args.results)),
                repeats=args.repeats,
                budget_usd=args.budget_usd,
                retry_errors=args.retry_errors,
                concurrency=args.concurrency,
                on_record=progress,
            )
        )
    except BudgetExceeded as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 2
    print(f"{len(done)} runs finished, ${sum(r.cost_usd for r in done):.4f} spent this session")
    return 0


def judge_identity(system: SystemConfig) -> str:
    """What verdicts are cached under: the judge's model (and provider), so
    renaming the system keeps them and changing the model asks again."""
    return f"{system.provider or system.api_base or ''}|{system.model}|{system.reasoning}"


def _judge_system(args: argparse.Namespace) -> SystemConfig:
    systems = {s.name: s for s in load_config(Path(args.config)).systems}
    if args.judge_system not in systems:
        sys.exit(f"no system named {args.judge_system!r} in {args.config}")
    return systems[args.judge_system]


def cmd_judge(args: argparse.Namespace) -> int:
    instances = _split_instances(args)
    records = list(ResultStore(Path(args.results)).latest().values())
    system = _judge_system(args)
    try:
        endpoints = asyncio.run(resolve_ui_providers([system]))
    except ProviderLookupError as exc:
        sys.exit(str(exc))
    if system.name in endpoints:
        system.use_endpoint(endpoints[system.name])
    if is_hosted(system) and args.budget_usd <= 0:
        sys.exit(f"{system.name} calls a hosted (paid) API; set --budget-usd to run it")
    pairs = candidate_pairs(instances, records, mode=args.mode, tolerance=args.tolerance)
    cache = VerdictCache(Path(args.cache))
    identity = judge_identity(system)
    print(f"{len(pairs)} candidate pairs ({args.mode}); judging with {system.name}")

    def progress(done: int, total: int) -> None:
        if done % 25 == 0 or done == total:
            print(f"  {done}/{total} judged")

    spent = asyncio.run(
        judge_pairs(
            pairs,
            llm_ask(system.endpoint()),
            cache,
            judge_model=identity,
            concurrency=args.concurrency,
            budget_usd=args.budget_usd,
            on_verdict=progress,
        )
    )
    missing = sum(1 for p in pairs if p.key(identity) not in cache.verdicts)
    print(f"${spent:.4f} spent; {missing} pairs still without a verdict")
    return 1 if missing else 0


def cmd_label(args: argparse.Namespace) -> int:
    instances = _split_instances(args)
    records = list(ResultStore(Path(args.results)).latest().values())
    system = _judge_system(args)
    pairs = candidate_pairs(instances, records, mode=args.mode, tolerance=args.tolerance)
    n = export_for_labelling(
        pairs,
        VerdictCache(Path(args.cache)),
        judge_identity(system),
        Path(args.out),
        n=args.n,
        seed=args.seed,
    )
    print(f"{n} pairs -> {args.out}; fill the same_issue column with yes or no")
    return 0


def cmd_agreement(args: argparse.Namespace) -> int:
    cache = VerdictCache(Path(args.cache))
    labels = read_labels(Path(args.labels))  # keyed like the cache, so no config is needed
    result = cohens_kappa(
        [(cache.verdicts[key][0], label) for key, label in labels.items() if key in cache.verdicts]
    )
    kappa = "n/a" if result.kappa is None else f"{result.kappa:.3f}"
    print(f"{result.n} labelled pairs: agreement {result.observed:.1%}, Cohen's kappa {kappa}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    instances = _split_instances(args)
    records = list(ResultStore(Path(args.results)).latest().values())
    judge = None
    if args.mode != "location":
        if not (args.config and args.judge_system and args.cache):
            sys.exit("--mode strict/lenient needs --config, --judge-system and --cache")
        judge = cached_judge(VerdictCache(Path(args.cache)), judge_identity(_judge_system(args)))
    try:
        summaries = write_report(
            Path(args.out),
            instances,
            records,
            baseline=args.baseline,
            title=args.title,
            data_fingerprint=fingerprint(instances),
            mode=args.mode,
            tolerance=args.tolerance,
            judge=judge,
        )
    except KeyError as exc:
        sys.exit(str(exc))
    print(f"{len(summaries)} systems -> {args.out} (+ .csv)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m revu_bench")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare-aacr", help="download and convert AACR-Bench")
    p.add_argument("--data-dir", default="data/aacr", help="raw release and fetched-PR cache")
    p.add_argument("--out", default="data/aacr/instances.jsonl")
    p.set_defaults(func=cmd_prepare_aacr)

    p = sub.add_parser("split", help="make the dev/test split")
    p.add_argument("--instances", required=True)
    p.add_argument("--dev-size", type=int, default=20)
    p.add_argument("--seed", type=int, default=20261008)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_split)

    p = sub.add_parser("config", help="write a starting systems config")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_config)

    for name, func in (
        ("run", cmd_run),
        ("report", cmd_report),
        ("judge", cmd_judge),
        ("label", cmd_label),
        ("agreement", cmd_agreement),
    ):
        p = sub.add_parser(name)
        p.add_argument("--instances", required=True)
        p.add_argument("--split")
        p.add_argument("--which", choices=["dev", "test"], default="dev")
        p.add_argument("--results", required=name != "agreement")
        p.set_defaults(func=func)
        if name in ("report", "judge", "label", "agreement"):
            p.add_argument("--judge-system", help="the judge's name in --config")
            p.add_argument("--cache", help="the judge's verdict cache (JSONL)")
            p.add_argument("--mode", choices=["location", "strict", "lenient"], default=None)
        if name in ("judge", "label"):
            p.add_argument("--config", required=True)
        if name in ("judge", "label"):
            p.add_argument("--tolerance", type=int, default=3)
        if name == "judge":
            p.add_argument("--budget-usd", type=float, default=0.0)
            p.add_argument("--concurrency", type=int, default=2)
        if name == "label":
            p.add_argument("--out", required=True)
            p.add_argument("--n", type=int, default=100)
            p.add_argument("--seed", type=int, default=20261101)
        if name == "agreement":
            p.add_argument("--labels", required=True)
        if name == "run":
            p.add_argument("--config", required=True)
            p.add_argument("--systems", help="comma-separated subset of system names")
            p.add_argument("--repeats", type=int, default=1)
            p.add_argument("--budget-usd", type=float, default=0.0)
            p.add_argument("--retry-errors", action="store_true")
            p.add_argument("--concurrency", type=int, default=1)
        elif name == "report":
            p.add_argument("--out", required=True)
            p.add_argument("--baseline")
            p.add_argument("--title", default="revu benchmark")
            p.add_argument("--tolerance", type=int, default=3)
            p.add_argument("--config", help="systems config holding the judge")

    args = parser.parse_args(argv)
    if getattr(args, "mode", "unset") is None:
        args.mode = "location" if args.command == "report" else "strict"
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
