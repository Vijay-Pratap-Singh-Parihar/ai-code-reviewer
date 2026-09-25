# Verification — Stages 0–7

Reproducible steps for what's already been verified in this branch. Each block is
copy-pasteable; expected output is noted inline. See [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)
for what each stage builds and why.

## Setup (once)

```bash
cp .env.example .env
uv sync --all-packages --group dev
```

## Static checks (all stages)

```bash
uv run ruff check .                                                              # → All checks passed!
uv run mypy packages/engine/src packages/db/src apps/api/src apps/worker/src    # → Success: no issues found
uv run pytest -v                                                                 # → 225 passed
```

`apps/api/tests/test_models_db.py`, `test_auth.py`, `test_analysis.py`, and
`apps/worker/tests/test_analyze.py` run against a real Postgres database, not mocks (the LLM
call itself is mocked in these — no API key or network access needed to run the suite). If
nothing is reachable at `TEST_DATABASE_URL` (default
`postgresql+psycopg://revu:revu_dev_password@localhost:5434/revu_test`) they **skip**
rather than fail — see "Stage 1" below to stand that database up. CI runs a disposable
Postgres service container for this on every push, so it's covered either way.

## Stage 0 — the app boots

```bash
docker compose build                 # builds api, worker, web images
docker compose up -d
docker compose ps                    # all 5 services should show healthy/running
curl -s http://localhost:8000/health # → {"status":"ok"}
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000   # → 200
```

If `5432`/`6379` are already used by something else on your machine, this repo's
Postgres/Redis are on `5434`/`6380` by default (`HOST_POSTGRES_PORT` / `HOST_REDIS_PORT`
in `.env` to change) — container-to-container traffic is unaffected either way.

## Stage 1 — schema and migrations

```bash
# One-time: a dedicated DB for the DB-backed tests, separate from the app's own `revu` DB
docker exec ai-code-reviewer-postgres-1 psql -U revu -d revu -c "CREATE DATABASE revu_test"

cd apps/api
DATABASE_URL_SYNC="postgresql+psycopg://revu:revu_dev_password@localhost:5434/revu" \
  uv run --project .. --package api alembic upgrade head
cd ../..

docker exec ai-code-reviewer-postgres-1 psql -U revu -d revu -c "\dt"
# → 16 rows: the 15 domain tables (organizations, users, ..., refresh_tokens) + alembic_version
```

Now `uv run pytest apps/api/tests/test_models_db.py -v` should show 7 passing tests
(previously skipped, if `revu_test` didn't exist yet).

## Stage 2 — auth, end to end, against the real running containers

```bash
docker compose build api worker
docker compose up -d api worker
docker compose logs worker --tail 5   # → "Starting worker for 2 functions: ping, db_ping"
```

```bash
COOKIE_JAR=$(mktemp)

# Signup — creates an org + owner user, returns an access token, sets the refresh cookie
curl -s -c "$COOKIE_JAR" -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"org_name":"Acme Inc","email":"verify@example.com","password":"correct-horse-battery"}'
# → 201-shaped JSON: {"access_token": "...", "token_type": "bearer", "expires_in": 900, "user": {...}}

ACCESS_TOKEN="<paste access_token from above>"

# /me with the access token
curl -s http://localhost:8000/auth/me -H "Authorization: Bearer $ACCESS_TOKEN"
# → {"id": "...", "org_id": "...", "email": "verify@example.com", "role": "owner"}

# /me with no token
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/auth/me   # → 401

# Refresh — rotates the cookie, issues a new access token
curl -s -b "$COOKIE_JAR" -c "$COOKIE_JAR" -X POST http://localhost:8000/auth/refresh
# → same shape as signup, different access_token

# Logout
curl -s -b "$COOKIE_JAR" -c "$COOKIE_JAR" -o /dev/null -w "%{http_code}\n" \
  -X POST http://localhost:8000/auth/logout   # → 204

rm -f "$COOKIE_JAR"
```

### Worker → Postgres wiring

```bash
uv run --package worker python - <<'PY'
import asyncio
from arq import create_pool
from arq.connections import RedisSettings

async def main():
    redis = await create_pool(RedisSettings.from_dsn("redis://localhost:6380/0"))
    job = await redis.enqueue_job("db_ping")
    print("db_ping result:", await job.result(timeout=10))

asyncio.run(main())
PY
# → db_ping result: ok
```

That confirms the worker container's own Postgres connection (set up in
`WorkerSettings.on_startup`) actually runs a query, not just that the container starts.

## Stage 3 — the reviewer, end to end, against a real LLM

Requires `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` set to a real key in `.env` (see
`REVU_MODEL_REVIEW` to change the model). Without a key, everything up to the LLM call still
works — the run ends up `status: "failed"` with a clear `error` message instead of findings.

```bash
docker compose build api worker
docker compose up -d --force-recreate api worker   # --force-recreate picks up a changed .env
```

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"org_name":"Acme Inc","email":"verify-stage3@example.com","password":"correct-horse-battery"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

DIFF='--- a/app.py\n+++ b/app.py\n@@ -1,4 +1,4 @@\n def total(items):\n     result = 0\n-    for i in range(len(items)):\n+    for i in range(len(items) + 1):\n         result += items[i]\n     return result\n'

RUN_ID=$(curl -s -X POST http://localhost:8000/analysis \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d "{\"repo_full_name\":\"acme/widgets\",\"head_sha\":\"abc1234\",\"pr_number\":1,\"pr_title\":\"Adjust loop bound\",\"pr_body\":\"Off-by-one change.\",\"diff\":\"$DIFF\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

sleep 6   # gives the worker time to pick the job up and call the LLM

curl -s http://localhost:8000/analysis/$RUN_ID -H "Authorization: Bearer $TOKEN"
```

Expected: `status: "succeeded"`, non-zero `tokens_in`/`tokens_out`/`cost_usd`/`latency_ms`, and
at least one finding on `app.py` around the changed loop bound (this exact diff produces a real
`IndexError` — a real Anthropic key returned this as a `critical`-severity, 0.98-confidence
finding when this was last run).

If `status` is still `"queued"` after several seconds, check `docker compose logs worker` — a
missing/invalid API key surfaces there and in the run's own `error` field once it fails.

### Multi-tenancy: repository ownership conflict

```bash
TOKEN_B=$(curl -s -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"org_name":"Other Org","email":"other-org@example.com","password":"correct-horse-battery"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/analysis \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN_B" \
  -d "{\"repo_full_name\":\"acme/widgets\",\"head_sha\":\"abc1234\",\"pr_number\":1,\"pr_title\":\"x\",\"diff\":\"x\"}"
# → 409 (acme/widgets is already registered under the first org)
```

## Stage 4 — the repository indexer (library only, no new endpoint)

Everything here runs via `uv run python`, not Docker/curl — Stage 4 is a `packages/engine`
library addition (`revu.index`), not wired into an API/worker endpoint yet (that's Stage 5).

```bash
uv run pytest packages/engine/tests/index packages/db/tests -v
# → 57 passed (54 index unit tests + the 10-hand-verified-edges checks + 3 db-package tests,
#   the last of which skips instead of failing if TEST_DATABASE_URL isn't reachable)
```

### Index this repository's own source and inspect the graph

```bash
uv run python - <<'PY'
from pathlib import Path
from revu.index import build_index_at_path

result = build_index_at_path(Path("."))
print(f"files={result.files_indexed} symbols={len(result.symbols)} "
      f"nodes={result.node_count} edges={result.edge_count}")
print(f"resolved calls={len(result.call_edges)} "
      f"unresolved calls={len([u for u in result.unresolved if u.kind == 'call'])}")

# A real cross-package edge: the worker calling into the Stage 3 reviewer.
worker_calls = [e for e in result.call_edges if e.caller.startswith("worker.")]
print(worker_calls[:3])
PY
# → files=61 symbols=201 nodes=262 edges=323
# → resolved calls=166 unresolved calls=779   (see IMPLEMENTATION_PLAN.md Stage 4 for the honest
#   breakdown of *why* — mostly calls into external libraries and through local variables,
#   both correctly out of scope for a no-type-inference, name-based resolver)
```

### The 10 hand-verified call edges

```bash
uv run pytest packages/engine/tests/index/test_verified_edges.py -v
```

Each edge in `packages/engine/tests/fixtures/verified_edges.yaml` was confirmed by hand against
the actual source line (see the `note` field on each entry); the test both resolves all 10 in
the built graph and independently re-checks that the claimed line still textually contains the
claimed callee name, so the fixture can't silently drift from the source it describes.

### Timing gate (cold build < 60s, incremental update < 5s, ~50k LOC)

```bash
uv run pytest packages/engine/tests/index/test_timing.py -v -s
# → cold build: 45762 LOC, 105 files, 2300 nodes, 2891 edges, ~1.4s
# → incremental update: 1 file(s) reparsed out of 105, ~0.4s
```

Uses the installed `pydantic` package (45,762 real LOC) as the ~50k LOC corpus — no bundled
fixture repo of that size exists, and this avoids a network fetch. See `test_timing.py`'s
docstring for why `pydantic` specifically.

### Incremental update on a real git repo

```bash
uv run python - <<'PY'
import tempfile
from pathlib import Path
from git import Repo
from revu.index import build_index, incremental_update

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    repo_dir = Path(tmp)
    repo = Repo.init(repo_dir)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "name", "Verify")
        cfg.set_value("user", "email", "verify@example.com")

    (repo_dir / "a.py").write_text("def helper():\n    return 1\n")
    repo.index.add(["a.py"])
    old_sha = repo.index.commit("first").hexsha
    old_result = build_index(repo_dir, old_sha)

    (repo_dir / "a.py").write_text("def helper():\n    return 2\n\ndef new_fn():\n    return helper()\n")
    repo.index.add(["a.py"])
    new_sha = repo.index.commit("second").hexsha

    incr = incremental_update(repo_dir, old_sha, new_sha, old_result)
    print("files reparsed:", incr.files_reparsed)           # → 1
    print("new symbol present:", "a.new_fn" in {s.qualified_name for s in incr.index.symbols})  # → True
    repo.close()
PY
```

(`ignore_cleanup_errors=True` and the explicit `repo.close()` sidestep a Windows-only GitPython
quirk where an open repo handle can briefly block deleting its own temp directory on `__exit__`
— harmless, but worth not lying about in a copy-pasteable snippet. The pytest suite itself is
unaffected: `tmp_path`'s teardown runs after each test's local `Repo` object is already out of
scope and garbage-collected.)

## Stage 5 — branch memory, end to end against Docker

```bash
uv run pytest packages/engine/tests/index/test_vcs.py apps/worker/tests/test_index_branch.py \
  apps/api/tests/test_branch_index.py -v
# → 35 passed (13 vcs + 12 worker/index_branch + 10 api/branch_index)
```

### Live, against the running containers

The worker needs the `git` CLI at runtime now (Stage 4's GitPython calls were never actually
exercised inside a container before this stage), so rebuild it:

```bash
docker compose build api worker
docker compose up -d --force-recreate api worker
docker compose logs worker --tail 5
# → "Starting worker for 4 functions: ping, db_ping, analyze_pr, update_branch_index"
```

`repo_path` in the trigger request must exist on the **worker container's** filesystem (there's no
GitHub App yet — see IMPLEMENTATION_PLAN.md Stage 5 — and no host volume mount for arbitrary repos),
so build a real throwaway git repo inside the container itself:

```bash
docker exec ai-code-reviewer-worker-1 sh -c '
  set -e
  rm -rf /tmp/demo-repo && mkdir -p /tmp/demo-repo && cd /tmp/demo-repo
  git init -q && git config user.name Demo && git config user.email demo@example.com
  mkdir -p pkg
  : > pkg/__init__.py
  printf "def helper():\n    return 1\n" > pkg/utils.py
  printf "from pkg.utils import helper\n\ndef run():\n    return helper()\n" > pkg/main.py
  git add -A && git commit -q -m "first commit" && git branch -M main
  git rev-parse HEAD
'
```

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"org_name":"Demo Org","email":"verify-stage5@example.com","password":"correct-horse-battery"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

TRIGGER=$(curl -s -X POST http://localhost:8000/repos/index \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"repo_full_name":"acme/demo-repo","branch_name":"main","repo_path":"/tmp/demo-repo"}')
echo "$TRIGGER"   # → status: "pending", head_sha: null — nothing usable is exposed until it's ready
REPO_ID=$(echo "$TRIGGER" | python3 -c "import sys,json; print(json.load(sys.stdin)['repo_id'])")

sleep 2
curl -s http://localhost:8000/repos/$REPO_ID/branches/main/index -H "Authorization: Bearer $TOKEN"
# → has_ready_index: true, head_sha resolved to the real commit, mode "full",
#   reason "first successful index build for this branch"
```

### Incremental update on a real commit, force-push detection, and manual rebuild

```bash
# A normal follow-up commit inside the container...
docker exec ai-code-reviewer-worker-1 sh -c '
  cd /tmp/demo-repo
  printf "from pkg.utils import helper\n\ndef run():\n    return helper()\n\ndef run_twice():\n    return run() + run()\n" > pkg/main.py
  git add -A && git commit -q -m "second commit"
'
curl -s -X POST http://localhost:8000/repos/index -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"repo_full_name":"acme/demo-repo","branch_name":"main","repo_path":"/tmp/demo-repo"}' > /dev/null
sleep 2
curl -s http://localhost:8000/repos/$REPO_ID/branches/main/index -H "Authorization: Bearer $TOKEN"
# → last_update.mode: "incremental", from_sha/to_sha both set, files_changed: 1

# ...then rewrite history (force-push shape) and trigger again:
docker exec ai-code-reviewer-worker-1 sh -c '
  cd /tmp/demo-repo
  git reset --hard HEAD~1
  printf "from pkg.utils import helper\n\ndef run():\n    return helper() * 100\n" > pkg/main.py
  git add -A && git commit -q -m "rewritten history"
'
curl -s -X POST http://localhost:8000/repos/index -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"repo_full_name":"acme/demo-repo","branch_name":"main","repo_path":"/tmp/demo-repo"}' > /dev/null
sleep 2
curl -s http://localhost:8000/repos/$REPO_ID/branches/main/index -H "Authorization: Bearer $TOKEN"
# → last_update.mode: "full", reason: "non-fast-forward update detected (force-push or rebase);
#   full rebuild" — the real force-push/rebase detector, not a heuristic guess

# Manual/forced rebuild reuses the same endpoint:
curl -s -X POST http://localhost:8000/repos/index -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"repo_full_name":"acme/demo-repo","branch_name":"main","repo_path":"/tmp/demo-repo","force_full":true}'
sleep 2
curl -s http://localhost:8000/repos/$REPO_ID/branches/main/index -H "Authorization: Bearer $TOKEN"
# → last_update.mode: "full", reason: "manual full rebuild requested"
```

### Confirm the row lifecycle directly in Postgres

```bash
docker exec ai-code-reviewer-postgres-1 psql -U revu -d revu \
  -c "SELECT status, head_sha, created_at FROM branch_index ORDER BY created_at;"
# → 4 rows, ALL status='ready' — older snapshots are kept, not deleted or overwritten,
#   confirming the versioned-row design (see IMPLEMENTATION_PLAN.md Stage 5) live.

docker exec ai-code-reviewer-postgres-1 psql -U revu -d revu \
  -c "SELECT mode, from_sha, to_sha, files_changed, reason FROM index_update_log ORDER BY created_at;"
# → full (first build) / incremental (from_sha set) / full (non-fast-forward) / full (manual)
```

Cleanup:

```bash
docker exec ai-code-reviewer-postgres-1 psql -U revu -d revu -c "DELETE FROM organizations WHERE name = 'Demo Org';"
docker exec ai-code-reviewer-worker-1 rm -rf /tmp/demo-repo
```

## Stage 6 — context retrieval (library only, no new endpoint)

Everything here runs via `uv run python`/`pytest`, not Docker/curl — Stage 6 is a `packages/engine`
library addition (`revu.context`), not wired into an API/worker endpoint yet (that's Stage 7).

```bash
uv run pytest packages/engine/tests/context -v
# → 42 passed, including test_integration.py's 4 hand-verified cases against this repo's own
#   real indexed graph (see IMPLEMENTATION_PLAN.md Stage 6 for exactly what was hand-verified)
```

### Build a context bundle for a real diff to this repository

```bash
uv run python - <<'PY'
from pathlib import Path
from revu.context import build_context_bundle, RetrievalConfig
from revu.index.graph import build_index_at_path

repo_root = Path(".")
graph = build_index_at_path(repo_root).graph

diff_text = """diff --git a/apps/api/src/api/services/repositories.py b/apps/api/src/api/services/repositories.py
--- a/apps/api/src/api/services/repositories.py
+++ b/apps/api/src/api/services/repositories.py
@@ -30,4 +30,4 @@ async def get_or_create_repository(
 ) -> Repository:
-    repo = await session.scalar(select(Repository).where(Repository.full_name == full_name))
+    repo = await session.scalar(select(Repository).where(Repository.full_name == full_name.strip()))
     if repo is not None:
"""

bundle = build_context_bundle(diff_text, graph, repo_root, config=RetrievalConfig(k=1))
print(f"items={len(bundle.items)} total_tokens={bundle.total_tokens}")
for item in bundle.items:
    print(f"  {item.file_path}:{item.line_start}-{item.line_end}  {item.retrieval_reason}")
PY
# → 4 items, 831 tokens: the changed function itself, its real 1-hop caller
#   (api.services.branch_index.trigger_index_build), and two real 1-hop callees —
#   each with a human-readable retrieval_reason, well under the default 8000-token budget
```

## Stage 7 — the cross-file agent (library only, no new endpoint)

Offline, mocked-LLM checks (no API key needed):

```bash
uv run pytest packages/engine/tests/agents packages/engine/tests/test_cross_file.py \
  packages/engine/tests/test_cross_file_integration.py -v
# → 38 passed: 30 tool tests, 7 loop-logic tests (round cap, retry-on-malformed-JSON,
#   bad-tool-argument handling), and the hand-verified integration test against this
#   repo's own real graph with a scripted "smart" fake LLM
```

### The real thing: diff_only vs. cross_file, against an actual model

Requires a real `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in `.env` — **this costs real money**
(~$0.03 for the run below). Build a small demo repo with a genuine cross-file bug:

On Windows, use a plain drive-rooted path instead of `/tmp` (e.g. `C:\tmp_demo_repo`, adjusting
`mkdir`/`cat` to `cmd`/`New-Item` equivalents) — `/tmp/...` gets translated by Git Bash for shell
commands but resolves differently for a native Windows Python process, so the two disagree on where
the repo actually is.

```bash
mkdir -p /tmp/demo_repo && cd /tmp/demo_repo
cat > inventory.py <<'PY'
"""Inventory helpers."""


class Item:
    def __init__(self, name: str, quantity: int) -> None:
        self.name = name
        self.quantity = quantity


def get_low_stock_items(items: list[Item]) -> dict[str, int]:
    """Items with fewer than 10 units left, keyed by name for O(1) lookup."""
    return {item.name: item.quantity for item in items if item.quantity < 10}
PY
cat > alerts.py <<'PY'
"""Low-stock alerting."""

from inventory import get_low_stock_items


def notify(message: str) -> None:
    print(message)


def send_low_stock_alerts(items):
    for item in get_low_stock_items(items):
        notify(f"Low stock: {item.name} ({item.quantity} left)")
PY
cd -
```

```bash
uv run python - <<'PY'
import asyncio
from pathlib import Path
from revu.agents import cross_file, diff_only
from revu.index.graph import build_index_at_path

DEMO = Path("/tmp/demo_repo")
MODEL = "claude-sonnet-5"
TITLE = "Return a dict from get_low_stock_items for O(1) lookup"
BODY = "Refactors get_low_stock_items to return a dict keyed by item name instead of a list."
DIFF = """diff --git a/inventory.py b/inventory.py
--- a/inventory.py
+++ b/inventory.py
@@ -8,5 +8,5 @@


-def get_low_stock_items(items: list[Item]) -> list[Item]:
-    \"\"\"Items with fewer than 10 units left.\"\"\"
-    return [item for item in items if item.quantity < 10]
+def get_low_stock_items(items: list[Item]) -> dict[str, int]:
+    \"\"\"Items with fewer than 10 units left, keyed by name for O(1) lookup.\"\"\"
+    return {item.name: item.quantity for item in items if item.quantity < 10}
"""

async def main():
    idx = build_index_at_path(DEMO)
    d = await diff_only.review_diff(pr_title=TITLE, pr_body=BODY, diff=DIFF, model=MODEL)
    print(f"diff_only: {len(d.findings)} findings, cost=${d.cost_usd}")
    for f in d.findings:
        print(f"  [{f.confidence}] {f.message[:100]}")

    cf = await cross_file.review_cross_file(
        pr_title=TITLE, pr_body=BODY, diff=DIFF, repo_root=DEMO, graph=idx.graph, model=MODEL,
    )
    print(f"\ncross_file: {len(cf.findings)} findings, cost=${cf.cost_usd}")
    for f in cf.findings:
        print(f"  [{f.confidence}] {f.message[:150]}")
        for e in f.evidence:
            print(f"    evidence: {e.file_path}:{e.line_start}-{e.line_end}")

asyncio.run(main())
PY
```

## Stage 8 — the verifier/aggregator (library only, no new endpoint)

Everything here runs via `uv run pytest`, not Docker/curl — Stage 8 is a `packages/engine` library
addition (`revu.verify`), not wired into an API/worker endpoint yet (tracked as deferred alongside
wiring `cross_file` itself, per `IMPLEMENTATION_PLAN.md` Stage 7/8).

```bash
uv run pytest packages/engine/tests/verify -v
# → 41 passed: dedup (12), evidence resolution (10 + the hand-verified integration test below),
#   confidence scoring (9), threshold+cap (5), and the full-pipeline orchestrator (4)
```

### The hand-verified evidence-resolution acceptance check

```bash
uv run pytest packages/engine/tests/verify/test_evidence_integration.py -v -s
```

Builds a real index of this repository's own working tree (the shared `real_repo_index`/
`real_repo_root` fixtures — no new redundant full-repo build), looks up `revu.models.Finding`'s
real, indexer-reported location, and hand-verifies it by reading the actual file at that line before
trusting it. Three findings go through `resolve_evidence`:

- one citing that real, hand-confirmed location — **must survive**
- one citing a file path that has never existed in this repository — **must be dropped**
- one citing the real file but a line number thousands of lines past its actual length —
  **must be dropped**

```
assert result.survived == [real_finding]
assert result.dropped_count == 2
assert result.drop_rate == 2 / 3
```

**What this proves, stated honestly:** the resolution mechanism itself correctly distinguishes a
genuine citation from a fabricated one against real data. It does **not** measure real-world
hallucination rates from an actual model — no LLM call is made anywhere in Stage 8 (forbidden by
this stage's own constraint; see `IMPLEMENTATION_PLAN.md`).

### Dedup, confidence, and threshold/cap on hand-constructed findings

```bash
uv run pytest packages/engine/tests/verify/test_dedup.py packages/engine/tests/verify/test_confidence.py \
  packages/engine/tests/verify/test_rank.py -v
```

Confirms, with hand-picked `Finding` objects (no real graph needed): two overlapping findings from
different agents merge into one with both messages preserved and evidence combined; genuinely
distinct findings (different file, different category, or lines too far apart) are never merged; a
finding two independent findings agreed on scores at least as confident as either alone; a finding
that lost evidence to resolution never outscores one that kept all of it; and the severity+confidence
cap never discards a critical finding to make room for a low-severity one.

### The full pipeline end to end

```bash
uv run pytest packages/engine/tests/verify/test_init.py -v
```

`verify_findings` wired end to end on hand-constructed findings against a real temp repo: two
agreeing findings merge and score higher than either alone; a finding with one fabricated evidence
item is dropped and the report's `evidence_drop_rate` reflects it; `VerificationConfig`'s `threshold`
and `max_findings` are honoured and reported via `dropped_below_threshold`/`cut_by_cap`.

Expected: `diff_only` returns a few speculative findings around confidence 0.7 with no cited
evidence; `cross_file` returns one high-confidence (~0.98) finding naming the real caller
(`alerts.send_low_stock_alerts`) and the exact runtime failure, with `evidence` pointing at both
`inventory.py` and `alerts.py` — the files it actually inspected via `find_callers`/`read_file`.
See `IMPLEMENTATION_PLAN.md`'s Stage 7 section for the exact output from the last real run.

## Pipeline wiring — `agent` selection on `POST /analysis`

```bash
uv run pytest apps/api/tests/test_analysis.py apps/worker/tests/test_analyze.py -v
# → 4 new API tests (422 on missing repo_path, 409 on no ready index — asserting nothing is
#   enqueued — and the happy-path enqueue) + 3 new worker tests (cross_file dispatch through
#   load_graph/review_cross_file/verify_findings, and the two defensive failure paths)
```

### Live, against the running containers — the non-LLM parts only

```bash
docker compose build api worker
docker compose up -d api worker
```

`sed` (not `python3`, which this Windows host doesn't have on PATH) pulls fields out of the JSON:

```bash
SIGNUP=$(curl -s -X POST http://localhost:8000/auth/signup -H "Content-Type: application/json" \
  -d '{"org_name":"Demo Org","email":"verify-pipeline@example.com","password":"correct-horse-battery"}')
TOKEN=$(echo "$SIGNUP" | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')

# 1. agent="cross_file" with no repo_path -> 422 at the schema layer, no DB/enqueue work at all.
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/analysis \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"repo_full_name":"acme/pipeline-demo","base_branch":"main","head_sha":"abc1234",
       "pr_number":1,"pr_title":"t","diff":"--- a\n+++ b\n","agent":"cross_file"}'
# → 422

# 2. agent="cross_file" with repo_path but no ready branch index -> 409, nothing enqueued.
curl -s -X POST http://localhost:8000/analysis \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"repo_full_name":"acme/pipeline-demo","base_branch":"main","head_sha":"abc1234",
       "pr_number":1,"pr_title":"t","diff":"--- a\n+++ b\n","agent":"cross_file",
       "repo_path":"/tmp/pipeline-demo"}'
# → 409, "no ready branch index for 'acme/pipeline-demo' @ 'main'; POST /repos/index ..."
```

**Deliberately not shown here as a live step:** posting a default `agent="diff_only"` request
against the running stack. Unlike the two checks above, that one *is* picked up by the real worker
and *does* make a real LLM call the moment it's processed — this was learned the hard way while
building this section (a stray verification call cost about $0.0008; see IMPLEMENTATION_PLAN.md's
"Pipeline wiring" section for the honest account). Anyone reproducing this locally should ask
themselves first, the same way Stage 3/7's live sections require explicit go-ahead before running.

**`agent="cross_file"` end to end** (real branch index + real graph + real model call) isn't
included here either, for the same reason plus the extra setup Stage 5's section above already
covers (`POST /repos/index` against a real git repo inside the worker container, waiting for
`has_ready_index: true`, *then* `POST /analysis` with `agent="cross_file"` and the matching
`repo_path`) — worth doing once as a real end-to-end check, but only with explicit permission for
the model call, same as every other real LLM verification in this project.

## Stage 9 (part 1) — frontend auth shell

```bash
cd apps/web
npm install
npm run lint
npm run test    # → 19 passed
npm run build   # production build (also what `docker compose build web` runs)
```

### Live, against the running containers

```bash
docker compose build api worker web
docker compose up -d api worker web
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/login   # → 200
```

Confirm `NEXT_PUBLIC_API_BASE_URL` actually got inlined into the production bundle at build time
(not just configured and silently ignored — see IMPLEMENTATION_PLAN.md's "Stage 9 (part 1)" section
for the real Dockerfile bug this catches):

```bash
docker exec ai-code-reviewer-web-1 sh -c "grep -rl 'localhost:8000' /app/.next/static | head -1"
# → a chunk path, proving the URL is baked into the client bundle
```

Exercise the real auth contract with `Origin: http://localhost:3000` (the browser's actual origin,
not just `localhost` with no origin — CORS behaves differently) against the real API, using `sed`
instead of `python3` for JSON extraction (see Stage 5's Windows-`python3` caveat above):

```bash
rm -f /tmp/web-cookies.txt
SIGNUP=$(curl -s -c /tmp/web-cookies.txt -X POST http://localhost:8000/auth/signup \
  -H "Origin: http://localhost:3000" -H "Content-Type: application/json" \
  -d '{"org_name":"Verify Web","email":"verify-web@example.com","password":"correct-horse-battery"}')
echo "$SIGNUP" | sed -n 's/.*"access_token":"\([^"]\{1,20\}\).*/token starts: \1.../p'

curl -s -b /tmp/web-cookies.txt -X POST http://localhost:8000/auth/refresh \
  -H "Origin: http://localhost:3000" -o /dev/null -w "refresh: %{http_code}\n"   # → 200

curl -s -b /tmp/web-cookies.txt -X POST http://localhost:8000/auth/logout \
  -H "Origin: http://localhost:3000" -o /dev/null -w "logout: %{http_code}\n"    # → 204

curl -s -b /tmp/web-cookies.txt -X POST http://localhost:8000/auth/refresh \
  -H "Origin: http://localhost:3000" -o /dev/null -w "refresh after logout: %{http_code}\n"  # → 401

rm -f /tmp/web-cookies.txt
docker exec ai-code-reviewer-postgres-1 psql -U revu -d revu \
  -c "DELETE FROM organizations WHERE name = 'Verify Web';"
```

**Windows-host caveat, reported honestly:** a fresh `npm install`/`npm ci` for this app can stall for
several minutes on some Windows hosts (confirmed here to be genuine host filesystem/network slowness,
not a real deadlock — an established HTTPS connection with near-zero CPU progress over minutes).
Killing and retrying mid-install can leave specific packages corrupted rather than just missing
(this happened twice: `zod`'s locale files, then `@next/swc-win32-x64-msvc`'s native binary) —
recoverable by removing and reinstalling just the affected package (`rm -rf node_modules/<pkg> &&
npm install`), not a full reinstall. `docker compose build web`'s own `npm ci`, which runs inside the
Linux container rather than on the Windows host, was not affected and completed in under a minute.
