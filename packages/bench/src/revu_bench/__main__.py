"""Command line: `uv run python -m revu_bench <command> ...`

split    make and save the dev/test split (commit the output file)
config   write a starting systems config
run      run systems over a split, resumably, within a budget
report   score results and write the markdown + CSV report
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from revu_bench.datasets import (
    fingerprint,
    load_instances,
    load_split,
    make_split,
    save_split,
    select,
)
from revu_bench.report import write_report
from revu_bench.runner import (
    BudgetExceeded,
    ResultStore,
    dump_config_template,
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


def cmd_report(args: argparse.Namespace) -> int:
    instances = _split_instances(args)
    records = list(ResultStore(Path(args.results)).latest().values())
    summaries = write_report(
        Path(args.out),
        instances,
        records,
        baseline=args.baseline,
        title=args.title,
        data_fingerprint=fingerprint(instances),
        tolerance=args.tolerance,
    )
    print(f"{len(summaries)} systems -> {args.out} (+ .csv)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m revu_bench")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("split", help="make the dev/test split")
    p.add_argument("--instances", required=True)
    p.add_argument("--dev-size", type=int, default=20)
    p.add_argument("--seed", type=int, default=20261008)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_split)

    p = sub.add_parser("config", help="write a starting systems config")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_config)

    for name, func in (("run", cmd_run), ("report", cmd_report)):
        p = sub.add_parser(name)
        p.add_argument("--instances", required=True)
        p.add_argument("--split")
        p.add_argument("--which", choices=["dev", "test"], default="dev")
        p.add_argument("--results", required=True)
        p.set_defaults(func=func)
        if name == "run":
            p.add_argument("--config", required=True)
            p.add_argument("--systems", help="comma-separated subset of system names")
            p.add_argument("--repeats", type=int, default=1)
            p.add_argument("--budget-usd", type=float, default=0.0)
            p.add_argument("--retry-errors", action="store_true")
            p.add_argument("--concurrency", type=int, default=1)
        else:
            p.add_argument("--out", required=True)
            p.add_argument("--baseline")
            p.add_argument("--title", default="revu benchmark")
            p.add_argument("--tolerance", type=int, default=3)

    args = parser.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
