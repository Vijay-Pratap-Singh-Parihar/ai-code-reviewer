# revu — Agentic Code Review Platform

See [`Product_Architecture_FullStack.md`](Product_Architecture_FullStack.md) for the product design and
[`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) for the build order actually being followed (an
app-first reorder of [`Execution_Roadmap_Weeks_0_to_18.md`](Execution_Roadmap_Weeks_0_to_18.md)).

## Layout

```
apps/api/        FastAPI application (auth, analysis trigger, routers, Alembic migrations)
apps/worker/     ARQ background worker (runs analysis jobs against the engine)
apps/web/        Next.js frontend
packages/engine/ "revu" — the review engine: providers, agents, indexer, context, verifier
packages/db/     "db" — shared SQLAlchemy models + declarative Base
```

`apps/api` and `apps/worker` both depend on `packages/engine` (`revu`) and `packages/db` (`db`)
as local uv workspace members, so engine code and the data model are each written once and
shared by both — neither app depends on the other.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed automatically by `uv python install 3.12`)
- [Docker](https://www.docker.com/) + Docker Compose
- Node.js 22+ (only needed for frontend work outside Docker)

## Environment

```bash
cp .env.example .env
```

Fill in at minimum `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` before the reviewer can make real LLM
calls. Provider/model config is env-based for now (`REVU_MODEL_REVIEW`) rather than the DB-backed
per-org `ai_providers`/`model_routes` design the schema already supports — that lands once the
Next.js frontend has a screen to manage encrypted credentials through (see `IMPLEMENTATION_PLAN.md`
Stage 3). Everything else has a working development default. Generate real secrets for
`JWT_SECRET_KEY` and `CREDENTIAL_ENCRYPTION_KEY` before this ever runs anywhere but your laptop —
the commands to generate them are commented next to each variable in `.env.example`.

## Run everything with Docker

```bash
docker compose up --build
```

- API: http://localhost:8000/health
- Web: http://localhost:3000
- Postgres: localhost:5434, Redis: localhost:6380 (overridable via `HOST_POSTGRES_PORT` /
  `HOST_REDIS_PORT` in `.env` — defaults avoid colliding with other local Postgres/Redis on
  5432/6379; containers still talk to each other on the standard internal ports)

## Local development (without Docker, for fast iteration)

```bash
# Python (api + worker + engine)
uv sync --all-packages --group dev
uv run pytest
uv run ruff check .
uv run mypy packages/engine/src packages/db/src apps/api/src apps/worker/src

# Run the API directly
uv run --package api uvicorn api.main:app --reload --app-dir apps/api/src

# Run the worker directly
uv run --package worker arq worker.settings.WorkerSettings --app-dir apps/worker/src

# Frontend
cd apps/web && npm install && npm run dev
```

Postgres and Redis still need to be reachable — either run `docker compose up postgres redis`
alongside local Python processes, or point `.env` at your own instances.

## Database migrations

`api.core.config.Settings` reads `DATABASE_URL_SYNC` from `postgres:5432` — the Docker-internal
hostname, correct for containers but unreachable from the host. Running Alembic from your host
machine (against `docker compose up postgres`, exposed on `HOST_POSTGRES_PORT`) needs that
overridden:

```bash
cd apps/api
DATABASE_URL_SYNC="postgresql+psycopg://revu:revu_dev_password@localhost:5434/revu" \
  uv run --project .. --package api alembic upgrade head
DATABASE_URL_SYNC="postgresql+psycopg://revu:revu_dev_password@localhost:5434/revu" \
  uv run --project .. --package api alembic revision --autogenerate -m "describe the change"
```

Inside a container (or anything that resolves the `postgres` service name), no override is
needed.

## Tests that touch the database

`apps/api/tests/test_models_db.py` runs real round-trip tests (enum storage, cascades, unique
constraints) against Postgres rather than mocking the ORM. It looks for `TEST_DATABASE_URL`
(default `postgresql+psycopg://revu:revu_dev_password@localhost:5434/revu_test`) and skips
itself — not fails — if nothing is reachable there. One-time setup against the compose Postgres:

```bash
docker exec ai-code-reviewer-postgres-1 psql -U revu -d revu -c "CREATE DATABASE revu_test"
```

CI runs a disposable `postgres:16-alpine` service container for this instead (see
`.github/workflows/ci.yml`), so these tests also run on every push.

## Repository indexer (Stage 4)

`revu.index` (`packages/engine/src/revu/index/`) builds a `rustworkx` call/import graph for a
Python repository: `git worktree` checkout at a commit, tree-sitter symbol extraction, import and
name-based call resolution, and an incremental update path that reparses only changed files. It's
a plain importable library at this stage — no CLI, no API endpoint yet (see
`IMPLEMENTATION_PLAN.md` Stage 4 for why, and Stage 5 for when that changes):

```python
from pathlib import Path
from revu.index import build_index, build_index_at_path, incremental_update

result = build_index(Path("/path/to/a/git/repo"), "abc1234")           # real commit, via git worktree
result = build_index_at_path(Path("/any/directory"))                    # no git required
print(result.node_count, result.edge_count, len(result.unresolved))
```

Call/import resolution is pure name-based matching with no type inference (a deliberate,
documented limitation — see `IMPLEMENTATION_PLAN.md` Stage 4 for real accuracy numbers measured
against this repository's own source, and why they're honestly reported as low for *this*
repo's shape rather than massaged).

## Triggering an analysis

There's no GitHub integration yet (Stage 10), so a caller supplies the diff directly:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"org_name":"Acme Inc","email":"me@example.com","password":"correct-horse-battery"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

RUN_ID=$(curl -s -X POST http://localhost:8000/analysis \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"repo_full_name":"acme/widgets","head_sha":"abc1234","pr_number":1,
       "pr_title":"Fix off-by-one","diff":"--- a/app.py\n+++ b/app.py\n..."}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

curl -s http://localhost:8000/analysis/$RUN_ID -H "Authorization: Bearer $TOKEN"
```

`repositories.full_name` is globally unique across the whole system (it models a real GitHub
repo, connectable to only one org's installation) — a second org posting the same
`repo_full_name` gets `409 Conflict`, not a silently misattributed run.

`agent` picks the reviewer: `"diff_only"` (default, no repository access needed, ~4x cheaper) or
`"cross_file"` (Stage 7's tool-calling agent, verified through Stage 8's evidence checker before
persisting — see IMPLEMENTATION_PLAN.md's "Pipeline wiring" section). `cross_file` requires a
`repo_path` (a path on the **worker container's** filesystem, same convention as Stage 5's branch
memory below) and an already-`ready` branch index for `(repo_full_name, base_branch)` built via
`POST /repos/index` — there's no on-demand indexing fallback, so a missing index is a `409`, not a
slow first request:

```bash
curl -s -X POST http://localhost:8000/analysis \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"repo_full_name":"acme/widgets","head_sha":"abc1234","pr_number":1,
       "pr_title":"Fix off-by-one","diff":"--- a/app.py\n+++ b/app.py\n...",
       "agent":"cross_file","repo_path":"/tmp/acme-widgets"}'
```

## Branch memory (Stage 5)

`BranchIndex`/`IndexUpdateLog` now have a real row lifecycle, wired end to end: trigger a build via
the API, the worker does the actual git/indexing work, results land in Postgres. Still no real
GitHub webhook (Stage 10) — the caller supplies a filesystem path to a git repository the **worker
process** can read, the same intentional simplification as Stage 3's "caller supplies the diff
directly."

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"org_name":"Acme Inc","email":"me@example.com","password":"correct-horse-battery"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# repo_path must exist on the *worker container's* filesystem, not the host's —
# see VERIFICATION.md's Stage 5 section for how to set up a throwaway repo there.
TRIGGER=$(curl -s -X POST http://localhost:8000/repos/index \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"repo_full_name":"acme/widgets","branch_name":"main","repo_path":"/tmp/demo-repo"}')
REPO_ID=$(echo "$TRIGGER" | python3 -c "import sys,json; print(json.load(sys.stdin)['repo_id'])")

# Poll until the build finishes
curl -s http://localhost:8000/repos/$REPO_ID/branches/main/index -H "Authorization: Bearer $TOKEN"
```

A manual/forced full rebuild reuses the same trigger endpoint with `"force_full": true` — there's no
separate endpoint for it. The status endpoint never reflects a build that's still in progress: while
one `BranchIndex` row is `building`, the previous `ready` row (or "no index yet" for a brand-new
branch) is what's returned, with `is_stale: true` noting a newer attempt is underway. See
`IMPLEMENTATION_PLAN.md`'s Stage 5 write-up for the full row-lifecycle design (why every build
attempt gets its own row instead of one row mutated in place), the force-push/non-fast-forward
detector, and two real bugs found and fixed while building this (one in Stage 4's incremental update,
one a Postgres `now()` transaction-scoping gotcha).

## Context / change-impact retrieval (Stage 6)

`revu.context` (`packages/engine/src/revu/context/`) turns a diff + a Stage 4/5 indexed graph into a
token-budgeted `ContextBundle` — the input Stage 7's agent will consume, not something exposed via
an API yet. Plain importable library, same pattern as the indexer:

```python
from pathlib import Path
from revu.context import build_context_bundle, RetrievalConfig
from revu.index.graph import build_index_at_path

graph = build_index_at_path(Path("/path/to/a/repo")).graph
bundle = build_context_bundle(diff_text, graph, Path("/path/to/a/repo"), config=RetrievalConfig(k=2))
print(bundle.total_tokens, [item.retrieval_reason for item in bundle.items])
```

No benchmark-driven recall curve exists in this track (see `IMPLEMENTATION_PLAN.md` Stage 6 for why)
— it's validated instead by a hand-verified case against this repository's own real indexed graph,
in the same spirit as Stage 4's 10 hand-verified call edges.

## Cross-file agent (Stage 7)

`revu.agents.cross_file.review_cross_file` — a Principal-Software-Architect-persona reviewer with
tools (`read_file`, `graph_query`, `find_definition`, `find_callers`) to inspect the real repository,
seeded with Stage 6's context bundle rather than exploring from zero. LangGraph-orchestrated; every
model call still goes through `revu.providers.llm.complete`. Plain importable library, not wired into
the live `/analysis` pipeline yet (see `IMPLEMENTATION_PLAN.md` Stage 7 for why that's deliberate):

```python
from pathlib import Path
from revu.agents.cross_file import review_cross_file
from revu.index.graph import build_index_at_path

repo_root = Path("/path/to/a/repo")
graph = build_index_at_path(repo_root).graph
result = await review_cross_file(
    pr_title=title, pr_body=body, diff=diff_text,
    repo_root=repo_root, graph=graph, model="claude-sonnet-5",
)
print(result.stopped_reason, [f.message for f in result.findings])
```

Validated three ways (see `IMPLEMENTATION_PLAN.md` Stage 7 for the full writeup): mocked loop-logic
tests, a hand-verified integration test against this repo's own real graph, and — the roadmap's
actual gate — a real, live, cost-approved comparison against `diff_only` on a genuine cross-file bug.
`diff_only` produced 3 speculative findings (confidence 0.7, no caller identified); `cross_file`
found the exact real caller and the exact runtime failure at confidence 0.98. Total cost: $0.031.

## Verifier / aggregator (Stage 8)

`revu.verify` (`packages/engine/src/revu/verify/`) post-processes a list of `Finding`s from one or
more agents: dedup near-duplicates, drop findings whose cited location(s) don't actually exist in
the repository (the hallucination check), recompute confidence from agent agreement + evidence
survival, then threshold and cap. Plain importable library, one entry point, not wired into the
live `/analysis` pipeline yet:

```python
from pathlib import Path
from revu.verify import verify_findings, VerificationConfig

result = verify_findings(
    findings,  # list[Finding], from one or more agents
    repo_root=Path("/path/to/a/repo"),
    config=VerificationConfig(threshold=0.5, max_findings=20),
)
print(result.report)          # counts in/out, merge count, evidence drop rate, threshold/cap cuts
print([f.message for f in result.findings])
```

No benchmark-driven precision/recall curve over threshold exists in this track (see
`IMPLEMENTATION_PLAN.md` Stage 8 for why) — the evidence-resolution mechanism is instead validated
by a hand-verified case against this repository's own real indexed graph: a finding citing a real,
hand-confirmed location survives, one citing a fabricated file path or an out-of-range line number
is correctly dropped, and the reported drop rate reflects it exactly.

```bash
uv run pytest packages/engine/tests/verify/test_evidence_integration.py -v
```

## Status

Build order and what's covered vs. deferred from the original roadmap: see
[`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md). For copy-pasteable steps to independently
verify what's already built, see [`VERIFICATION.md`](VERIFICATION.md).
