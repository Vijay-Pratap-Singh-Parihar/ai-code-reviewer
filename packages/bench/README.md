# revu-bench

The benchmark harness for revu. It implements the evaluation methodology in
[`docs/research/literature-review.md`](../../docs/research/literature-review.md) §10, so every
number in the dissertation comes from code that can be rerun.

| Methodology | Where |
|---|---|
| AACR-Bench adapter: pinned release (checksum), diffs and PR descriptions fetched once from GitHub and cached | `adapters/aacr.py` |
| Systems that use a provider configured on the AI Providers screen | `providers.py` |
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
# 1. Build the AACR-Bench instances (downloads the pinned release, then two GitHub API reads
#    per PR, cached in data/aacr/cache; uses GITHUB_TOKEN or the GitHub CLI's login).
uv run python -m revu_bench prepare-aacr
#    The committed split (30 dev / 154 test, stratified by language) is splits/aacr.split.json;
#    make a new one only for different data:
uv run python -m revu_bench split --instances data/aacr/instances.jsonl --dev-size 30 --out splits/aacr.split.json

# 2. Describe the systems to compare (edit the template).
uv run python -m revu_bench config --out bench/systems.json

# 3. Run. Local models are free; hosted ones refuse to start without a budget.
uv run python -m revu_bench run --instances data/aacr/instances.jsonl --split splits/aacr.split.json --which dev \
  --config bench/systems.json --results bench/dev-results.jsonl --repeats 3 --budget-usd 2.00

# 4. Report, comparing everything with a baseline.
uv run python -m revu_bench report --instances data/aacr/instances.jsonl --split splits/aacr.split.json --which dev \
  --results bench/dev-results.jsonl --baseline B0-local-qwen --out bench/dev-report.md
```

- **Resumable:** results are appended to the JSONL as each PR finishes. Rerunning skips finished
  PRs (`--retry-errors` re-runs failures), so an interrupted run never pays twice.
- **Budgeted:** a system without a local `api_base` is treated as a paid API and won't start with
  a zero budget. The run stops once the recorded spend crosses `--budget-usd`.
- **Keys:** a system can name a provider configured on the AI Providers screen
  (`"org": "Acme", "provider": "Groq"`, with the model id as typed in the UI). It is read under
  that organisation's row-level security and decrypted as the worker does, using `DATABASE_URL`
  and `CREDENTIAL_ENCRYPTION_KEY`, so the key never leaves the UI. Without revu's database, a
  system can instead name the environment variable holding its key (`api_key_env`). Keys never go
  into config or result files.
- **Data integrity:** the split file records a fingerprint of the instances and their ground
  truth, and `run`/`report` refuse a split made for different data.

## AACR-Bench data

Release `alibaba/aacr-bench@ee9d7fe` (Apache-2.0), `dataset/positive_samples.json`: 196 PRs in
10 languages with 1,506 expert-verified reference comments, each labelled with the context needed
to raise it (Diff, File or Repo level).

- **Diff:** GitHub's three-dot comparison of `source_commit...target_commit`, which reproduces the
  release's `change_line_count`.
- **Excluded (12 PRs, 6.1%):** their `target_commit` no longer exists on GitHub (an intermediate
  commit on a contributor's fork, since squashed or force-pushed away), so their diff can't be
  rebuilt: ComfyUI ×2, node, uv, kestra, ClickHouse ×2, keycloak ×5. `prepare-aacr` lists them.
  **184 PRs and 1,407 reference comments remain** (717 Diff, 473 File, 217 Repo level).
- **Negative samples** (`negative_samples.json`) are not used: the release doesn't document them.
- Converted data and fetched diffs stay out of git (`data/` is ignored): they are third-party
  content from 184 repositories, reproducible exactly with `prepare-aacr`.

## Not yet

- Adapters for a second dataset (SWR-Bench or CodeFuse-CR-Bench) to check generalisation.
- Six test-set diffs exceed 90k characters (a generated test file, `pnpm-lock.yaml`, test
  snapshots); production revu should drop lockfiles and generated files before reviewing.
- Cross-file runs, which need a repository checkout at the base commit and a built index.
- The LLM judge for `strict`/`lenient` matching and its validation against human labels (Cohen's κ).
- Context recall@budget (§10.3), which needs no LLM and runs on the index alone.
