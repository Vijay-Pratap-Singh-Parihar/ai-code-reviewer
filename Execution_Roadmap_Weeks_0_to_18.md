# Execution Roadmap — Weeks 0 to 18

**Purpose.** This is the document you work from daily and hand to Claude Code phase by phase. Each phase has an objective, a task list, concrete acceptance checks, and a binary gate. **Do not start a phase until the previous gate passes.**

---

## How to use this

1. At the start of each phase, paste that phase's **Brief for Claude Code** block into a fresh session.
2. Work the task list. Tick items as they land.
3. Run the **Acceptance checks** verbatim. They must all pass.
4. Confirm the **Gate**. It is binary — there is no "mostly".
5. Commit, tag the repo `phase-N-complete`, and record the date and any deviation in `DECISIONS.md`.

If a gate fails twice, stop and re-scope rather than pushing forward. A failed gate in week 5 is information; a failed gate discovered in week 13 is a crisis.

---

## Week map

| Week | Phase | Gate |
|---|---|---|
| 0 | Setup | Repo scaffolded, `CLAUDE.md` written, CI green |
| 1 (days 1–3) | 0 — Spike | One instance's diff + ground truth + parsed AST printed |
| 1–2 | 1 — Harness | Null reviewer scores 0.0/0.0, logged to disk |
| 3 | 2 — Vertical slice | Real F1 for diff-only reviewer on dev subset |
| 4–5 | 3 — Indexer | 10/10 hand-verified call edges; 50k LOC < 60s |
| 6–7 | 4 — Change impact | Context recall@budget curve plotted, no LLM used |
| 8–9 | 5 — Agents | Cross-file agent beats diff-only on repo-level recall |
| 10 | 6 — Verifier | Precision improves; P/R curve over threshold |
| 11 | 7 — Baselines | All external baselines scored on dev subset |
| 12–13 | 8 — Experiments | **Numbers locked.** All tables regenerate from one script |
| 14–18 | 9 — Application | Live PR receives correct inline comments |
| 19–20 | 10 — Study + writing | *To be planned after week 13* |

---

## Week 0 — Setup

**Objective.** A repository Claude Code can work in productively.

### Tasks

- [ ] Create repo, `uv init`, Python 3.12, pin every dependency
- [ ] Scaffold the package layout (see Build Plan §Repository layout)
- [ ] Write `CLAUDE.md`: architecture summary, graph schema, module boundaries, coding conventions, what not to do
- [ ] Create `DECISIONS.md` — one line per design decision with date and reason
- [ ] `pytest` + `ruff` + `mypy` configured; GitHub Actions running all three
- [ ] `.env.example` with provider keys; real `.env` gitignored
- [ ] `configs/` directory with a `null.yaml` placeholder

### Acceptance checks

```bash
uv run pytest            # passes (zero tests is fine)
uv run ruff check .      # clean
uv run mypy src/         # clean
git log --oneline        # CI green on the first push
```

**Gate.** A fresh clone runs `uv sync && uv run pytest` successfully.

> **Brief for Claude Code — Week 0**
> Scaffold a Python 3.12 project named `revu` using uv. Create the package layout in `src/revu/` with subpackages `index`, `context`, `agents`, `verify`, `deliver`, `eval`, plus `models.py` and `cli.py`. Add pytest, ruff and mypy config to `pyproject.toml`, and a GitHub Actions workflow running all three on push. Create `configs/`, `tests/`, `scripts/`. Do not implement any logic yet — scaffolding only.

---

## Phase 0 — Feasibility spike (Week 1, days 1–3)

**Objective.** Disprove your assumptions cheaply. **Do this by hand.** Do not delegate — you need to understand the data shape well enough to specify everything that follows.

### Tasks

- [ ] Clone `alibaba/aacr-bench`; load it; document the exact schema in `DECISIONS.md`
- [ ] Confirm each ground-truth comment carries its context-level label (Diff / File / Repo). **If this label is absent, your headline result is not measurable and you must re-plan now.**
- [ ] Reconstruct one instance's repository at its base commit; apply the diff
- [ ] Parse one file with tree-sitter; extract all function definitions with line ranges. **Verify the current grammar-loading API** — it has changed across recent versions and most tutorials are stale
- [ ] Make one LiteLLM call; log tokens in, tokens out, latency, cost
- [ ] Run `alibaba/open-code-review` and `ozyyshr/RepoGraph` once each on a small repo; note what works and what breaks

### Acceptance checks

```bash
uv run python scripts/spike.py --instance 0
# must print: the diff, the ground-truth comments with context labels,
# and the function definitions extracted from one changed file
```

**Gate.** That script runs and prints all three. Record tree-sitter and grammar versions in `DECISIONS.md`.

**If it fails:** fall back to CodeFuse-CR-Bench and re-plan Phase 1 around its format. Better now than in November.

---

## Phase 1 — Data contracts and harness (Weeks 1–2)

**Objective.** Measurement infrastructure that works before anything is being measured.

### Tasks

- [ ] `models.py` — `Instance`, `Finding`, `ContextBundle`, `RunResult` as Pydantic v2 models
- [ ] `eval/loader.py` — benchmark JSON → `list[Instance]`, **preserving per-comment context-level labels**
- [ ] `eval/matching.py` — decide whether a predicted finding matches a ground-truth comment. Implement the benchmark's own protocol so numbers stay comparable
- [ ] `tests/test_matching.py` — hand-constructed match and non-match cases, including edge cases (adjacent lines, same line different file, semantically similar different issue)
- [ ] `eval/metrics.py` — precision, recall, F1, localisation accuracy, each also stratified by context level
- [ ] `eval/logger.py` — append-only JSONL: prompt, response, tokens, latency, cost, config id, timestamp
- [ ] `configs/` — YAML schema; config id is a hash of the file
- [ ] `eval/runner.py` + `cli.py` — `revu eval --config X --split dev`
- [ ] Split the benchmark: 20-instance `dev`, remainder `test`. **Commit the split file.**
- [ ] `NullReviewer` returning an empty finding list

### Acceptance checks

```bash
uv run pytest tests/test_matching.py -v          # all pass
uv run revu eval --config configs/null.yaml --split dev
# → precision 0.0, recall 0.0, F1 0.0 over 20 instances
ls runs/                                          # JSONL written
```

**Gate.** The null reviewer scores exactly zero and the run is logged to disk.

**Keep yourself:** the *definition* of what counts as a match. Delegate the code, own the rule.

> **Brief for Claude Code — Phase 1**
> Implement the evaluation harness for `revu`. Start with `src/revu/models.py` containing Pydantic v2 models: `Instance` (repo_url, base_commit, diff, pr_title, pr_body, linked_issue, ground_truth_comments each with a context_level field), `Finding` (file_path, line_start, line_end, category, severity, message, evidence list, confidence, agent_name), `ContextBundle` (items with file/line-range/content/retrieval_reason/score, total_tokens), `RunResult` (instance_id, config_id, findings, tokens_in, tokens_out, cost_usd, latency_ms).
> Then: `eval/loader.py` for AACR-Bench JSON; `eval/matching.py` implementing [paste the matching rule you decided in Phase 0]; `eval/metrics.py` with precision/recall/F1/localisation, each also computed stratified by context_level; `eval/logger.py` writing append-only JSONL; `eval/runner.py` and a Typer CLI `revu eval --config --split`.
> Write `tests/test_matching.py` with hand-constructed match and non-match cases first, before the matching implementation. Finally add a `NullReviewer` returning no findings, and `configs/null.yaml`.

---

## Phase 2 — Vertical slice (Week 3)

**Objective.** One deliberately bad reviewer, working end to end. This is baseline #1.

### Tasks

- [ ] `agents/diff_only.py` — single LLM call with PR title, body, diff
- [ ] Prompt instructs JSON array matching the `Finding` schema
- [ ] Structured parsing with retry on malformed JSON; **log every parse failure**
- [ ] LiteLLM wrapper with exact token accounting
- [ ] Wire as a config; run on dev subset
- [ ] Read 50 findings by hand; write observations into `DECISIONS.md`

### Acceptance checks

```bash
uv run revu eval --config configs/diff_only.yaml --split dev
# → a real, non-zero F1. Write the number down.
uv run python scripts/parse_failure_rate.py     # reports % malformed JSON
```

**Gate.** A recorded F1 number for the diff-only baseline on the dev subset.

**Expect:** low precision, hallucinated line numbers, generic style advice. That confirms your problem statement rather than contradicting it.

---

## Phase 3 — Repository indexer (Weeks 4–5)

**Objective.** A code property graph, built once and updated incrementally.

### Tasks — in dependency order

- [ ] `index/checkout.py` — `git worktree` materialisation at a given commit
- [ ] `index/walker.py` — respect `.gitignore`, skip vendored dirs, cap file size
- [ ] `index/symbols.py` — tree-sitter queries → functions, classes, methods, module assignments, each with qualified name and line range
- [ ] `index/imports.py` — module dependency edges. **Time-box to one week.** Log unresolved references rather than chasing them
- [ ] `index/calls.py` — resolve call sites by qualified name and import scope. **Do not attempt type inference**
- [ ] `index/inherit.py` — inheritance and implementation edges
- [ ] `index/tests.py` — test-to-symbol edges by naming convention and import
- [ ] `index/cochange.py` — co-change edges from `git log`
- [ ] `index/graph.py` — assemble in `rustworkx`
- [ ] `index/store.py` — persist to SQLite as node and edge tables
- [ ] `index/incremental.py` — reparse only changed files, patch the subgraph
- [ ] `tests/fixtures/verified_edges.yaml` — 10 hand-verified call relationships from a real repo

### Acceptance checks

```bash
uv run pytest tests/test_index.py -v            # 10/10 verified edges resolve
uv run revu index --repo <50k-loc-repo>         # cold build < 60s
uv run revu index --repo <same> --incremental   # warm update < 5s
uv run revu index-stats --repo <same>           # nodes, edges, unresolved-symbol rate
```

**Gate.** All 10 hand-verified call edges resolve correctly, and both timing thresholds are met. Record both timings — they are thesis results.

**Highest risk in the project.** Import resolution in dynamically typed languages is genuinely hard. An 80%-accurate graph that exists beats a perfect graph that does not. Report your resolution rate as a stated limitation.

> **Brief for Claude Code — Phase 3**
> Implement the repository indexer in `src/revu/index/`. Build in this order and stop after each module for review: checkout via git worktree; file walker honouring gitignore; tree-sitter symbol extraction for Python and [second language] producing qualified names with line ranges; import resolution (log unresolved references to a list rather than raising); call-edge resolution by qualified name and import scope — name-based only, no type inference; inheritance edges; test-to-symbol edges; co-change edges from git log; graph assembly in rustworkx; SQLite persistence with node and edge tables; incremental update that reparses only changed files.
> Add `revu index`, `revu index --incremental` and `revu index-stats` CLI commands. Write `tests/test_index.py` that loads `tests/fixtures/verified_edges.yaml` and asserts each listed caller→callee relationship exists in the graph.

---

## Phase 4 — Change-impact analysis (Weeks 6–7)

**Objective.** Your core contribution. **Validated with zero LLM spend.**

### Tasks

- [ ] `context/diff.py` — parse diff into hunks as (file, line range, kind)
- [ ] `context/mapping.py` — hunks → graph nodes by line containment and overlap
- [ ] `context/traverse.py` — bounded k-hop expansion. **Make every edge type individually toggleable** — this gives you ablations for free
- [ ] `context/rank.py` — hybrid of inverse graph distance, BM25 over diff tokens, code-embedding cosine. Weights configurable
- [ ] `context/budget.py` — greedy knapsack under token budget, costed with a real tokeniser not a character estimate
- [ ] `context/bundle.py` — assemble with a `retrieval_reason` recorded per item
- [ ] `eval/context_recall.py` — for every repo-level ground-truth comment, does the bundle contain the file it refers to?
- [ ] `scripts/sweep_context.py` — sweep k and budget, emit the curve

### Acceptance checks

```bash
uv run python scripts/sweep_context.py --split dev --k 1,2,3 --budget 4k,8k,16k,32k
# → context_recall curve written to results/, plotted to figures/
# → zero LLM calls made (assert cost_usd == 0 in the run log)
```

**Gate.** The context recall@budget curve exists and is plotted. If recall is poor here, your agents cannot possibly find those defects — fix retrieval before proceeding.

**This gate is also a thesis result.** Reporting the retrieval curve separately from end-to-end performance is the decomposition most papers in this space skip.

---

## Phase 5 — Agent layer (Weeks 8–9)

**Objective.** Specialised, tool-using agents over the context bundle.

### Tasks

- [ ] **Tools first.** `agents/tools/`: `read_file`, `graph_query`, `find_definition`, `find_callers`, `git_log`, `run_semgrep`. Each returns structured data, not prose. Unit-test each
- [ ] `agents/cross_file.py` — the cross-file consistency agent, fully working, alone
- [ ] Measure it against the diff-only baseline **before writing any other agent**
- [ ] Then: `intent.py`, `correctness.py`, `security.py`, `convention.py`, `test_adequacy.py`
- [ ] `agents/graph.py` — LangGraph parallel fan-out with checkpointing
- [ ] `agents/conventions.py` — mine repo-specific patterns from git history, not generic style rules
- [ ] `PROMPTS.md` — changelog of every prompt revision

### Acceptance checks

```bash
uv run revu eval --config configs/crossfile_only.yaml --split dev
# → repo-level recall exceeds configs/diff_only.yaml
uv run revu eval --config configs/all_agents.yaml --split dev
```

**Gate.** The cross-file agent alone beats the diff-only baseline on **repository-level** recall.

**This is a deliberate hypothesis test at week 9.** If graph context plus a specialised agent does not improve cross-file detection, your central premise is wrong and you still have eleven weeks to reframe. A well-evidenced negative result with a clean retrieval-curve decomposition is still a thesis.

**Expect most of this phase to be prompt iteration, not code.** Claude Code helps less here. Budget accordingly.

---

## Phase 6 — Verifier and aggregator (Week 10)

**Objective.** Your precision lever, and likely your most striking result.

### Tasks

- [ ] `verify/dedup.py` — fuzzy match on file, overlapping range, category; merge messages
- [ ] `verify/evidence.py` — resolve every cited location against the graph; drop unresolvable findings. **Log the drop rate** — it measures hallucination directly
- [ ] `verify/confidence.py` — from agent agreement, evidence count, severity
- [ ] `verify/rank.py` — threshold τ, then cap at N by severity and confidence
- [ ] `scripts/sweep_threshold.py` — P/R curve over τ

### Acceptance checks

```bash
uv run revu eval --config configs/no_verifier.yaml --split dev
uv run revu eval --config configs/full.yaml --split dev
# → precision measurably higher with the verifier
uv run python scripts/sweep_threshold.py --split dev
# → precision/recall curve plotted
```

**Gate.** Precision improves over the unverified pipeline, and the P/R curve over τ is plotted. Report the precision gain and the recall cost separately — the trade-off is the finding.

---

## Phase 7 — External baselines (Week 11)

**Objective.** The comparisons that make your numbers meaningful.

### Tasks

- [ ] `eval/baselines/no_context.py` — diff only (already have)
- [ ] `eval/baselines/bm25.py` — RepoCoder-style lexical retrieval
- [ ] `eval/baselines/dense.py` — embedding retrieval
- [ ] `eval/baselines/agentic.py` — free file exploration, no graph
- [ ] `eval/baselines/external.py` — subprocess wrappers for `open-code-review` and PR-Agent, normalising their output into `Finding`
- [ ] Normalise all baseline outputs through the same matching function

### Acceptance checks

```bash
uv run revu eval --config configs/baseline_*.yaml --split dev
# → every baseline produces a scored result row
```

**Gate.** All six baselines scored on the dev subset, comparable through one matching function.

**Note:** `open-code-review` is Go. You invoke its compiled binary; you do not need to write Go.

---

## Phase 8 — Experiments (Weeks 12–13) — **the critical milestone**

**Objective.** Numbers locked. Everything after this is presentation.

### Tasks

- [ ] Estimate total cost of the grid **before launching**; check against budget
- [ ] Cost-tier: full sweep on an economical model, headline config on a frontier model
- [ ] Full grid: all configs × 3 runs × **test split**, with resume-on-failure
- [ ] Ablations: no graph, no verifier, each agent removed, each edge type removed
- [ ] Sweeps: k, token budget, τ
- [ ] Stratified recall by context level — **your headline result**
- [ ] Statistics: Wilcoxon signed-rank, Cliff's delta, mean ± SD
- [ ] Cost curve: F1 against tokens per PR, all methods
- [ ] `scripts/make_tables.py` regenerating every table and figure from logged JSONL

### Acceptance checks

```bash
uv run python scripts/make_tables.py
# → every table in results/ and figure in figures/ regenerated
uv run python scripts/make_tables.py --verify
# → byte-identical output on re-run
```

**Gate.** Every number that will appear in your thesis regenerates from logged data by running one script. If a number cannot be regenerated, it is a liability.

**Tag the repo `numbers-locked`.** From here, the engine is frozen except for bugs.

---

## Weeks 14–18 — Application layer

Engine frozen. Wrap it.

| Week | Deliverable | Gate |
|---|---|---|
| **14** | Postgres schema + Alembic, FastAPI skeleton, JWT auth with refresh rotation, ARQ worker, engine wrapped as a job | Trigger an analysis via `curl`, poll status, get findings back — no UI |
| **15** | GitHub App registered, webhook receiver with signature verification, installation flow, repo selection, index triggered via API | A real webhook from a test repo enqueues a job |
| **16** | Branch memory: snapshot storage, incremental update on push, **merge-base resolution**, staleness states, force-push detection. Budget gate + token ledger | Merging to a tracked branch updates the index incrementally, not fully; verified in `index_update_log` |
| **17** | Next.js: auth, dashboard, **PR analysis view with diff, findings, and evidence trail** | The evidence trail renders: this finding came from these files via these edges |
| **18** | Providers screen, usage screen, branch memory screen, comment posting to GitHub, deploy | **A real PR on a live repo receives correct inline comments.** Screenshot everything |

**Cut order if behind:** org/roles, audit log, usage screen, providers screen (hardcode one), history screen. **Never cut the PR analysis view with the evidence trail** — it is your viva demo and it is the only screen that makes your research contribution visible.

---

## Weeks 19–20 — To be planned

Human annotation study, qualitative failure analysis, and writing. Plan this in week 13 once you know what your numbers actually say — the shape of the study depends on where your system wins and loses.

---

## Standing rules for every Claude Code session

Put these in `CLAUDE.md` so they apply without restating:

- Never delete or rewrite files under `runs/`. Run logs are append-only and irreplaceable.
- Every LLM call logs prompt, response, tokens in/out, cost, config id, timestamp.
- No hardcoded model names outside `configs/`. Everything is config-driven.
- Pydantic models in `models.py` are the contract. Changing one requires updating the harness and a migration note in `DECISIONS.md`.
- Tests before implementation for anything in `eval/`. The harness must be trustworthy.
- Pin every dependency exactly. The reproducibility appendix depends on it.
- No new dependency without a line in `DECISIONS.md` saying why.

## Delegation split

| Phase | Give to Claude Code | Keep yourself |
|---|---|---|
| 0 | Nothing | All of it |
| 1 | Models, logger, CLI, loader, metrics | The matching rule |
| 2 | Parsing, retry, wiring | Prompt content, error analysis |
| 3 | Tree-sitter queries, persistence, graph assembly | Graph schema; the 10 verified edges |
| 4 | Traversal, BM25, embeddings, knapsack | Scoring weights; definition of context recall |
| 5 | Tool implementations, LangGraph wiring | Every prompt; the agent decomposition |
| 6 | Dedup, serialisation | Evidence-gate rules, threshold policy |
| 7 | Baseline wrappers, output normalisation | Which baselines are fair |
| 8 | Sweep orchestration, table generation | Which experiments to run, what they mean |
| 14–18 | Nearly all of it — CRUD, auth, UI, plumbing | Branch-memory semantics, evidence-trail design |
