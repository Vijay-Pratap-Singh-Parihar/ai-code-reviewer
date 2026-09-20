# Implementation Plan — App-First Reorder

**Source documents:** [`Product_Architecture_FullStack.md`](Product_Architecture_FullStack.md), [`Execution_Roadmap_Weeks_0_to_18.md`](Execution_Roadmap_Weeks_0_to_18.md)

The roadmap builds the research engine for 13 weeks before touching the application (weeks 14–18). We are inverting that: build the full production app (FastAPI + Postgres + ARQ + Next.js + Docker) end to end first, wired to a deliberately simplified engine, then deepen the engine in place, stage by stage, until it matches the roadmap's Phase 3–6 design. Nothing from the roadmap's engine work is skipped — it is reordered to come after there's a running system to deepen.

**Locked decisions for this track:**
- Indexer language support: **Python only** (no JS/TS grammar work).
- GitHub integration: **stubbed** — analysis is triggered via a plain REST endpoint, not real GitHub App webhooks, until Stage 10.
- The academic benchmarking track (AACR-Bench, external baselines, ablations, statistics, thesis tables) is **out of scope** for this track entirely — see "Not covered" below. It's a separate, parallel effort if ever needed.

---

## Stage map

| Stage | What it builds | Roadmap origin | Status |
|---|---|---|---|
| 0 | Repo scaffold: package layout, Docker Compose (Postgres/Redis/api/worker/web), `.env.example`, Alembic, pytest/ruff/mypy, CI | Week 0 (Setup) | ✅ done |
| 1 | Core Pydantic models (`Finding`, `ContextBundle`, `RunResult`) + SQLAlchemy schema from the architecture doc's data model | Roadmap Phase 1 `models.py` + App Week 14 schema | ✅ done |
| 2 | FastAPI skeleton, JWT auth (access + rotating refresh), ARQ worker, health checks | App Week 14 | ✅ done |
| 3 | Simplified reviewer: single LiteLLM call over PR title/body/diff, JSON-schema output, wrapped as a background job | Roadmap Phase 2 (`diff_only` agent), adapted | ✅ done |
| 4 | Basic Python indexer: git worktree checkout, file walker, tree-sitter symbols, import edges, name-based call edges, `rustworkx` graph, Postgres persistence, incremental update | Roadmap Phase 3 | ✅ done |
| 5 | Branch memory: `branch_index` / `index_update_log` tables live, staleness states, force-push → full rebuild detection, merge-base resolution (triggered via API call, not a real push webhook yet) | App Week 16 + Phase 3 incremental design | Not started |
| 6 | Change-impact / context retrieval: diff→hunk mapping, bounded k-hop traversal, token-budgeted knapsack, `retrieval_reason` per context item | Roadmap Phase 4 | Not started |
| 7 | Cross-file agent + tool layer (`read_file`, `graph_query`, `find_definition`, `find_callers`), replacing the diff-only reviewer as the default | Roadmap Phase 5 (subset) | Not started |
| 8 | Verifier/aggregator: dedup, evidence resolution against the graph, confidence scoring, threshold + comment cap | Roadmap Phase 6 | Not started |
| 9 | Next.js frontend: auth, dashboard, **PR analysis view with diff + findings + evidence trail** | App Week 17 | Not started |
| 10 | Real GitHub App: registration, webhook receiver + signature verification, installation flow — replaces the manual trigger from Stage 3 | App Week 15 | Not started |
| 11 | Remaining screens: providers, usage/budget, branch memory UI, comment-posting back to GitHub | App Week 18 | Not started |
| 12 | Token/cost controls: triage-before-inference, pre-flight estimate gate, content-addressed result cache, tiered model routing, quota ledger | Architecture §3 | Stretch, after 0–11 |

Each stage ships with unit tests and a Docker-runnable acceptance check before moving to the next. We will pause between stages for review rather than running all of them unattended.

---

## Coverage against the original roadmap

### Covered, just reordered
- Week 0 setup → Stage 0
- Phase 1 `models.py` → Stage 1
- Phase 2 diff-only reviewer → Stage 3 (kept as the initial "simplified engine", not discarded once the real agents land in Stage 7 — it becomes the cheap screening tier)
- Phase 3 indexer (symbols, imports, calls, incremental update) → Stage 4, Python-only
- Phase 4 change-impact/context retrieval → Stage 6
- Phase 5 agent layer → Stage 7 (cross-file agent only; `intent`/`correctness`/`security`/`convention`/`test_adequacy` agents and LangGraph fan-out are extensions on top of Stage 7, added if/when needed)
- Phase 6 verifier/aggregator → Stage 8
- App Week 14 (Postgres/FastAPI/ARQ skeleton) → Stages 0–2
- App Week 15 (GitHub App) → Stage 10
- App Week 16 (branch memory) → Stage 5
- App Week 17 (Next.js + evidence trail) → Stage 9
- App Week 18 (remaining screens, deploy) → Stage 11

### Explicitly not covered by this track
- **Phase 0** — feasibility spike against `alibaba/aacr-bench` (benchmark schema check, running `open-code-review`/`RepoGraph`). This is thesis validation work, not app functionality.
- **Phase 1's harness half** — `eval/loader.py`, `eval/matching.py`, `eval/metrics.py` against benchmark ground truth, dev/test split. We keep `Finding`/`RunResult` models (Stage 1) but not the benchmark-scoring machinery.
- **Phase 7** — external baseline wrappers (BM25, dense retrieval, free-exploration agentic baseline, `open-code-review`/PR-Agent subprocess wrappers).
- **Phase 8** — the full experiment grid, ablations, sweeps, Wilcoxon/Cliff's-delta statistics, thesis table/figure regeneration scripts.
- **Weeks 19–20** — human annotation study and dissertation writing.
- **Co-change edges, test-to-symbol edges, inheritance edges** (Phase 3 sub-tasks) — deferred past Stage 4's MVP; added later only if the product needs them.
- **Multi-language indexing** (JS/TS grammars) — Python only, per the locked decision above.
- **`developer_action` / Outdated-Rate tracking** — needs real GitHub comment/resolution webhooks, so it waits for Stage 10.
- **Multi-tenant polish**: full org/role management, audit log UI — the architecture doc itself says to keep these minimal; we'll have the `org_id` column and enforcement at the query layer from Stage 1, but no roles/permissions UI until explicitly requested.

If any of the "not covered" items turn out to matter to you later (e.g. you decide you do need the benchmark numbers for a report), they slot in as additional stages without disrupting what's built here.

---

## Stage 0 — done

Built: uv workspace (`packages/engine` = `revu`, `apps/api`, `apps/worker`) on Python 3.12, Next.js 15 app in `apps/web`, `docker-compose.yml` (Postgres 16, Redis 7, api, worker, web), Alembic wired to an empty `Base.metadata`, ruff + mypy(strict) + pytest configured, GitHub Actions CI (`engine-and-api` + `web` jobs), `.env.example`, `.gitignore`.

Verified locally:
- `uv sync --all-packages --group dev` resolves the workspace, `revu` installs as a path dependency into both `api` and `worker`.
- `uv run ruff check .`, `uv run mypy packages/engine/src apps/api/src apps/worker/src`, `uv run pytest` all pass (3/3 tests).
- `docker compose build` builds all three custom images; `docker compose up -d` brings up all five services; Postgres and Redis report healthy; `GET /health` on the API returns `{"status": "ok"}`; the worker connects to Redis and registers its `ping` job; the Next.js app serves its default page on port 3000.
- Host ports for Postgres/Redis were moved to 5434/6380 (overridable via `HOST_POSTGRES_PORT`/`HOST_REDIS_PORT` in `.env`) since 5432/6379 were already in use by an unrelated local project — container-to-container traffic still uses the standard internal ports, so this only affects connecting from the host machine.

## Stage 1 — done

Built:
- `packages/engine/src/revu/models.py` — the engine's Pydantic contract: `Severity`, `FindingCategory`, `EvidenceItem`, `Finding`, `ContextItem`, `ContextBundle`, `RunResult`. This is what an agent/pipeline run produces in memory; it's distinct from the persisted DB rows below by design (`Finding` vs. `FindingRecord`, `ContextBundle` vs. `ContextBundleRecord`) so a Pydantic contract change and a DB migration are never accidentally conflated.
- `apps/api/src/api/models/` — the full data model from `Product_Architecture_FullStack.md` §5: `organizations`, `users`, `github_installations`, `repositories`, `tracked_branches`, `branch_index`, `index_update_log`, `ai_providers`, `model_routes`, `pull_requests`, `analysis_runs`, `findings`, `context_bundles`, `token_usage_ledger`, `audit_log`. UUID primary keys, `org_id` FKs throughout for the multi-tenancy enforcement point, JSONB for the semi-structured columns (`config_json`, `evidence_json`, `permissions`, `metadata`).
- `api.models._enum.pg_enum()` — every enum column stores the `StrEnum` **value** ("high", "admin") rather than SQLAlchemy's default member **name** ("HIGH", "ADMIN"), so raw SQL and the JSON/API layer use the same vocabulary. Caught and fixed before the first migration was applied, with a regression test guarding it.
- First Alembic migration (`initial schema`), generated via autogenerate and applied to the running dev Postgres — all 15 tables verified present via `\dt`.
- `apps/api/tests/test_models_db.py` + `conftest.py` — real round-trip tests against a dedicated `revu_test` Postgres database (enum storage, cascade delete, JSONB round-trip, unique constraints), not mocks. Skips gracefully (doesn't fail) if no test DB is reachable. CI now runs a `postgres:16-alpine` service container so these run on every push too.

Verified: `ruff check .`, `mypy --strict`, and `pytest` (21/21) all pass; the migration applies cleanly against the dockerized Postgres.

## Stage 2 — done

Built:
- `api.core.security` — bcrypt password hashing; JWT access tokens (15 min, HS256); opaque refresh tokens (never a JWT — a random `secrets.token_urlsafe`, only its SHA-256 hash ever touches the database).
- `refresh_tokens` table (`api/models/auth.py`) — not in the architecture doc's table list, added here because "rotating refresh (httpOnly cookie)" needs somewhere to track rotation state. Each row can point at the row that replaced it (`replaced_by_id`), which is what makes reuse detection possible.
- `api.services.auth` — signup (creates org + owner user), login, and **rotation with reuse detection**: presenting an already-rotated-away refresh token doesn't just fail, it revokes every other active token for that user, on the assumption that a reused token means it was stolen.
- `api/routers/auth.py` — `POST /auth/signup`, `/login`, `/refresh`, `/logout`, `GET /auth/me`. Refresh token travels only as an httpOnly, `SameSite=Lax` cookie scoped to `/auth`; the access token is returned in the response body for the client to hold in memory (never in a cookie, never in `localStorage`, per the architecture doc's explicit warning).
- `api/core/deps.py` — `CurrentUser` dependency: decodes the bearer token and re-fetches the user row (not just trusting the JWT claims), so a deleted/deactivated user is rejected within one 15-minute window instead of until the token's natural expiry.
- ARQ worker now connects to Postgres too (`WorkerSettings.on_startup`/`on_shutdown`), proven by a `db_ping` job that runs `SELECT 1` through it.
- Fixed a second correctness bug while building this: every timestamp column was `TIMESTAMP WITHOUT TIME ZONE`, but all application code produces `datetime.now(UTC)` — comparing/writing those together either raises or silently drops the timezone. Every timestamp column is now `TIMESTAMPTZ` (`api.models.mixins.TZDateTime`), migrated via a second Alembic revision alongside the new `refresh_tokens` table.
- `apps/api/tests/test_auth.py` — 9 tests driving the **real FastAPI app** through `httpx.AsyncClient` against real Postgres (SQLAlchemy's `create_savepoint` join mode wraps each test, including the app's own internal `commit()` calls, in a transaction that's rolled back after). Covers signup, duplicate-email rejection, login success/failure, `/me` auth enforcement, rotation, reuse detection (including that reuse revokes the *whole* chain, not just the presented token), and logout.

Verified beyond the test suite: rebuilt the Docker images and ran the entire signup → `/me` → refresh → logout flow with `curl` against the actually-running containerized API (not just local `uv run`), and enqueued `db_ping` through Redis against the live worker container — both round-tripped correctly. `ruff`, `mypy --strict`, and `pytest` (30/30) all pass.

## Stage 3 — done

**Architectural change made along the way:** the SQLAlchemy models described in Stage 1 as living under `apps/api/src/api/models/` were extracted into a new shared workspace package, **`packages/db`** (importable as `db`). Stage 3 needed the worker to read/write `AnalysisRun`/`FindingRecord` rows directly, and the worker depending on the whole `apps/api` package (FastAPI, uvicorn, routers, everything) just to reach its models would have been exactly the kind of coupling "clean architecture" rules out. Both `apps/api` and `apps/worker` now depend on `packages/db` as a workspace member, the same way both already depended on `packages/engine` (`revu`). Stage 1's file paths in this document are now historical — the tables and their design are unchanged, only their location moved.

Built:
- `revu.providers.llm` — the LiteLLM completion wrapper (`packages/engine`), normalising token/cost accounting per the architecture doc's §7 requirement. Sets `litellm.drop_params = True` — found live that Claude's newer models reject an explicit `temperature=0.0`, and providers differ enough on accepted params that failing the call over one of them is worse than dropping it.
- `revu.agents.diff_only` — the simplified reviewer itself: one LLM call over PR title/body/diff, structured JSON output validated against a Pydantic schema, with **one retry on malformed/schema-invalid JSON** and a logged, non-fatal give-up (empty findings) if the retry also fails — per the roadmap's Phase 2 requirement to log every parse failure rather than crash.
- `AnalysisRequest`/`AnalysisRunPublic` schemas, `api.services.analysis`, `POST /analysis` + `GET /analysis/{run_id}` — the ad-hoc trigger. The caller supplies the diff directly (no GitHub fetch exists yet); this is a named, documented simplification undone in Stage 10.
- `worker.jobs.analyze.analyze_pr` — loads the run, calls `review_diff`, persists `FindingRecord` rows, updates status/tokens/cost/latency. Registered in `WorkerSettings`.
- `api.core.queue.get_redis_pool` — a lazy, lock-guarded ARQ connection pool exposed as a FastAPI dependency (not a bare module import) specifically so tests can override it, the same pattern as `get_db`.
- **Provider/model configuration stays env-based** (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `REVU_MODEL_REVIEW`) for now, snapshotted onto `AnalysisRun.config_snapshot` at enqueue time. This was a deliberate choice, not an oversight: the `ai_providers`/`model_routes` tables and envelope encryption already exist in the schema for the real per-org, DB-backed design, but there's no UI/UX surface to manage encrypted credentials through until the Next.js frontend (Stage 9) exists. Revisit this when that frontend work starts.
- **Two real bugs caught by live testing, not by the automated suite** (both fixed, both now regression-tested):
  1. `litellm.UnsupportedParamsError` on `claude-sonnet-5` for `temperature=0.0` — fixed via `drop_params`.
  2. `repositories.full_name` is globally unique (correctly modelling the real GitHub App design: one repo, one org's installation), but the ad-hoc analysis endpoint silently reused another org's existing repository row on a name collision — a second org's analysis run got attached to the *first* org's repo, silently invisible to its own creator. Fixed with an explicit `RepositoryOwnedByAnotherOrgError` → `409 Conflict`, with a regression test (`test_trigger_analysis_conflicts_when_repo_owned_by_another_org`).
- `packages/engine/tests/test_llm.py`, `test_diff_only.py` — mocked-LLM unit tests (no network, no API key needed) covering token/cost extraction, the retry-on-malformed-JSON path, and schema-violation handling.
- `apps/api/tests/test_analysis.py` — real-Postgres, real-app tests via the same `api_client` fixture pattern as auth: trigger/enqueue, auth enforcement, 404 on unknown/other-org runs, 422 on invalid input, the 409 conflict regression test.
- `apps/worker/tests/test_analyze.py` + new `apps/worker/tests/conftest.py` — the job tested directly against real Postgres with `review_diff` mocked, including a "run already gone" no-op case and a failure path that marks the run `FAILED` with the exception message recorded.

Verified beyond the test suite (52/52 passing): rebuilt and restarted the Docker containers, then ran the **entire pipeline against a real Anthropic API key** — signup → `POST /analysis` with a real off-by-one diff → worker picked the job up via Redis → real LLM call → `GET /analysis/{id}` returned a real finding (`critical`, confidence 0.98, correctly explaining the resulting `IndexError`) with real token/cost/latency accounting (453 in / 131 out / $0.0022 / 6.3s).

## Stage 4 — done

Built `packages/engine/src/revu/index/`:

- `checkout.py` — `git worktree add --detach` (via GitPython) into a disposable temp
  directory, with a `worktree_checkout` context manager that always cleans up (including a
  fallback plain-filesystem removal + `git worktree prune` if `worktree remove` itself fails).
  Never touches the caller's actual checked-out branch.
- `walker.py` — if the root is a git working tree, uses `git ls-files -- '*.py'` directly
  (tracked files only, so `.gitignore` is respected for free); otherwise falls back to a plain
  `os.walk` with a hard-coded vendored/binary directory skip-list (`.git`, `node_modules`,
  `__pycache__`, `.venv`, `dist`, `build`, ...) and a root-level-only `.gitignore` matcher.
  Files over 1MB are skipped. The fallback path exists specifically so the indexer can also run
  against a plain directory that isn't a git repo (used for the timing benchmark below).
- `symbols.py` — tree-sitter queries (`function_definition`/`class_definition`) find every
  definition; qualified names are built by walking each definition's *parent chain* in the
  parsed tree to find enclosing class/function ancestors, not by relying on query nesting. A new
  `discover_source_roots`/`module_name_for_file` pair detects real Python import roots (`src/`
  layout or flat layout, by finding `__init__.py` and walking up while parents are still
  packages) so qualified names match real import paths — `revu.index.symbols`, not
  `packages.engine.src.revu.index.symbols` — which turned out to matter a lot for import/call
  resolution accuracy (see below).
- `imports.py` — handles plain `import x[.y] [as z]`, `from x[.y] import a[, b as c]`, relative
  imports with correct dot-counting (distinguishing a package's own `__init__.py` from a regular
  submodule, since `from .` means something different in each), and lazy imports inside function
  bodies (walks the whole tree, not just top-level statements). Star imports and relative imports
  that climb above the repo root are logged to `UnresolvedRef`, not guessed.
- `calls.py` — pure name-based resolution, **no type inference**, per the roadmap. Resolves: a
  bare name against the module's top-level definitions or its import scope; `self.method()`
  against the enclosing class (a static fact of Python method dispatch, not inference);
  `module_alias.func()` through a whole-module import; `ClassName.static_method()` through a
  class name bound in scope (again static, not inference — the base is a name, not a variable).
  Does **not** resolve: calls through a local variable, attribute chains deeper than one hop, or
  anything the pure name/import scope doesn't cover — each logged with a specific `reason`
  (`"call through a local variable requires type inference"`, `"python builtin"`,
  `"external import (module not indexed)"`, etc.) rather than a generic "failed".
- `graph.py` — two-pass assembly: pass 1 parses every file for symbols + raw import edges; pass
  2 (needing the complete cross-file symbol table) resolves import targets and call sites, then
  builds a `rustworkx.PyDiGraph`. One node per module plus one per function/class/method; every
  edge's payload is tagged `{"kind": "imports"}` or `{"kind": "calls"}` (not a single
  undifferentiated edge type) so a later ablation stage can selectively traverse by edge kind.
- `store.py` — **design decision**: serialise the `PyDiGraph` with `pickle` + `gzip` to a local
  file (`REVU_INDEX_STORAGE_DIR`, default `.revu/index-graphs`), storing the file path in
  `BranchIndex.graph_ref` (already a `String(512)` column from Stage 1 — clearly meant to hold a
  reference, not a blob itself). `Product_Architecture_FullStack.md` §5 offered this or
  normalised node/edge Postgres tables ("benchmark both; blob is simpler, tables allow SQL
  queries over structure") — Stage 4 has no feature yet that needs to `SELECT` over graph
  structure (that's Stage 6+), so the blob is the right call now. **Also decided:** `store.py`
  does not import `db.BranchIndex` directly and does not write rows itself — `packages/db`
  depends on `packages/engine`, so the reverse import would be circular. Instead it exposes
  `to_branch_index_fields(...)`, a plain dict shaped to the model's columns; the actual row
  lifecycle (create/update, staleness transitions, force-push rebuild detection) is Stage 5's job
  per the stage map above, run from `apps/worker` where both packages are already available.
  `packages/db/tests/test_index_store_integration.py` proves this hand-off dict round-trips
  through a real `BranchIndex` row against real Postgres now, rather than leaving it unverified
  until Stage 5.
- `incremental.py` — `git diff --name-only <old> <new>` filtered to `.py` files, then re-parses
  *only those files* (via a shared single-file parse helper, not a full directory walk) and
  re-assembles the whole graph from `unchanged symbols/edges (kept) + changed files (re-parsed)`.
  Chosen over surgical in-place graph patching because graph assembly itself is pure
  dict/list bookkeeping — fast — while the actually expensive part (re-parsing/re-walking every
  file) is what's skipped; this is simpler and much harder to get subtly wrong.
  **Known, documented limitation:** if a changed file renames/removes a symbol, call edges *from
  unchanged files* into that old symbol silently disappear (the target node is gone) rather than
  being flagged as newly-unresolved — the caller-side file would need reprocessing too to notice.
  A full rebuild is the only way to guarantee every edge reflects the latest state everywhere.
- No CLI wiring. This app-first track has never built a `cli.py` (Stages 0–3 are all
  API/worker-driven), and the task itself flagged not to over-invest here — `revu.index` exposes
  plain importable functions (`build_index`, `build_index_at_path`, `incremental_update`,
  `save_graph`/`load_graph`) via `revu/index/__init__.py`. Whether Stage 5 wires these behind an
  API endpoint, a worker job, or an actual CLI is an open decision left to that stage.

**A real bug found and fixed while testing:** `imports.py`'s handling of `from x import a, b`
originally compared tree-sitter nodes with Python `is` to skip re-counting the module-name node
itself while walking the statement's children — but tree-sitter's Python bindings return a fresh
wrapper `Node` object on each access, so `is` never matched (`==`/`.id` does; confirmed by
inspection). This silently double-counted the imported module's own name as a bogus extra
"imported symbol" on **every** `from`-import statement in every file, inflating import-edge
counts and occasionally producing an accidentally-resolved phantom edge when the bogus name
happened to collide with a real module. Fixed by comparing `.id` instead of `is`; a regression
test (`test_from_import_multiple_names`) now asserts the exact edge count. Indexing this
repository's own source before/after the fix: import edges dropped from 606 to 403 (the correct
number) and resolved import edges from 245 to 157 — the fix removed noise, not signal.

**Honest accuracy numbers (self-indexing this repository, 61 files, 201 symbols):**
- Import edges: 403 total, 157 (39%) resolved to a module this indexing run also discovered —
  the rest are legitimate external/stdlib imports (`fastapi`, `sqlalchemy`, `pydantic`, `os`,
  ...), which is expected for an application that's mostly plumbing around third-party
  frameworks, not a resolution failure.
- Call sites: 945 total, 166 (17.6%) resolved to a concrete symbol. Breakdown of the rest:
  383 external-module attribute calls (`session.commit()`, `response.set_cookie()` — calls into
  FastAPI/SQLAlchemy/etc., correctly out of scope), 281 calls through a local variable
  (would need type inference, explicitly excluded per the roadmap), 48 calls to a name imported
  from an external module, 29 attribute chains deeper than one hop, 28 builtins, 6 genuinely
  unresolved names, and 4 cases that resolved to a real external base class method
  (`Model.model_validate` — inherited from Pydantic's `BaseModel`, not defined in the subclass,
  so correctly not found in the indexed symbol set).
  **This 17.6% headline number is low compared to the roadmap's "80%-accurate" framing, and
  that's reported honestly rather than massaged** — this repository is a thin, heavily-OOP
  FastAPI/SQLAlchemy application where the majority of calls are into external libraries or
  through ORM-session-shaped local variables, which is close to a worst case for a
  no-type-inference, single-repo indexer. The 10 hand-verified edges below are all same-repo,
  cross-module/cross-package calls — exactly the category this indexer is built to get right —
  and all 10 resolve correctly. A codebase with more of its own call graph and fewer
  framework-mediated calls would score meaningfully higher; this number should be read as "worst
  realistic case for this specific repo's shape," not a general accuracy claim.

**Verified:**
- `packages/engine/tests/index/` — 54 unit tests across all eight modules using synthetic
  fixture sources (git worktrees via a real temp repo for `checkout.py`, tree/dir fixtures for
  `walker.py`, inline Python snippets for `symbols.py`/`imports.py`/`calls.py`, small
  multi-file synthetic projects for `graph.py`, a real temp git repo with two real commits for
  `incremental.py`, round-trip tests for `store.py`).
- `packages/engine/tests/fixtures/verified_edges.yaml` + `test_verified_edges.py` — 10
  hand-verified call relationships from this repository's own source (read by hand against the
  actual line before being added: router → service, service → ORM model construction, and
  worker → engine/db across package boundaries). All 10 resolve; a second test independently
  re-confirms each recorded line still textually contains the claimed callee name, so the
  fixture can't silently drift from the source it claims to describe.
- `packages/engine/tests/index/test_timing.py` — the roadmap's timing gate, against the
  installed `pydantic` package (45,762 real LOC, 105 files) as a stand-in for "a ~50k LOC repo"
  (no bundled 50k LOC fixture repo exists, and this avoids a network fetch): **cold build in
  1.45s** (budget 60s) and **incremental update in 0.36s** after a one-file change (budget 5s),
  both measured, not estimated.
- `packages/db/tests/test_index_store_integration.py` — real-Postgres round-trip proving
  `to_branch_index_fields(...)` produces a valid `BranchIndex` row (against `revu_test`, skips
  gracefully if unreachable, same pattern as Stage 1's DB tests).
- `uv run ruff check .`, `uv run mypy packages/engine/src packages/db/src apps/api/src
  apps/worker/src`, and `uv run pytest` (107/107) all pass.
- No Docker rebuild — Stage 4 is a `packages/engine` library addition only, not wired into any
  API endpoint or worker job yet, so there's no running service whose image would change.

## Next action

Stage 5: branch memory — give `BranchIndex`/`IndexUpdateLog` a real row lifecycle (staleness
states, force-push → full rebuild detection, merge-base resolution), triggered via an API call
that invokes Stage 4's `build_index`/`incremental_update` from `apps/worker` (where both `revu`
and `db` are available together) and writes the row using `store.to_branch_index_fields`. Also
the natural point to decide whether indexing gets its own API endpoint, a worker job, or stays
purely internal until an actual caller (Stage 6's context retrieval) needs it.
