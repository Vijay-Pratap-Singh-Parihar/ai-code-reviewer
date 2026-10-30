# revu-bench

The benchmark harness for revu. It implements the evaluation methodology in
[`docs/research/literature-review.md`](../../docs/research/literature-review.md) §10, so every
number in the dissertation comes from code that can be rerun.

| Methodology | Where |
|---|---|
| §10.1 Canonical data, committed dev/test split (stratified by language, fixed seed, data fingerprint) | `datasets.py` |
| §10.2 Matching: one-to-one maximum matching; `location` (same file, ±3 lines), `strict` (+ judge), `lenient` (same file + judge) | `matching.py` |
| §10.3 Precision / recall / F1 (micro and macro), **recall by Diff / File / Repo**, noise rate, findings per PR, ECE and Brier, cost per PR, latency p50/p95 | `metrics.py` |
| §10.6 Paired bootstrap CIs, Wilcoxon signed-rank (exact for small n), Holm correction, Cliff's δ | `stats.py` |
| Resumable, budget-capped runs through revu's own engine | `runner.py` |
| Markdown + CSV report | `report.py` |

The unit of analysis is the pull request: per-PR scores first, aggregates after, and statistics
resample or rank PRs. A failed run counts as a PR where nothing was found, so a flaky system can't
look better by skipping hard PRs.

## Use

```bash
# 1. Convert a benchmark to the canonical JSONL (one BenchInstance per line), then split it.
uv run python -m revu_bench split --instances data/aacr.jsonl --dev-size 20 --out data/aacr.split.json

# 2. Describe the systems to compare (edit the template).
uv run python -m revu_bench config --out bench/systems.json

# 3. Run. Local models are free; hosted ones refuse to start without a budget.
uv run python -m revu_bench run --instances data/aacr.jsonl --split data/aacr.split.json --which dev \
  --config bench/systems.json --results bench/dev-results.jsonl --repeats 3 --budget-usd 2.00

# 4. Report, comparing everything with a baseline.
uv run python -m revu_bench report --instances data/aacr.jsonl --split data/aacr.split.json --which dev \
  --results bench/dev-results.jsonl --baseline B0-local-qwen --out bench/dev-report.md
```

- **Resumable:** results are appended to the JSONL as each PR finishes. Rerunning skips finished
  PRs (`--retry-errors` re-runs failures), so an interrupted run never pays twice.
- **Budgeted:** a system without a local `api_base` is treated as a paid API and won't start with
  a zero budget. The run stops once the recorded spend crosses `--budget-usd`.
- **Keys:** a hosted system names the environment variable holding its key (`api_key_env`). Keys
  never go into config or result files. The product itself takes keys only from the AI Providers
  screen; this is offline research tooling.
- **Data integrity:** the split file records a fingerprint of the instances and their ground
  truth, and `run`/`report` refuse a split made for different data.

## Not yet

- Dataset adapters (AACR-Bench, Martian Code Review Bench, SWR-Bench), written once each release's
  exact format has been inspected.
- Cross-file runs, which need a repository checkout at the base commit and a built index.
- The LLM judge for `strict`/`lenient` matching and its validation against human labels (Cohen's κ).
- Context recall@budget (§10.3), which needs no LLM and runs on the index alone.
