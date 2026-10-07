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
packages/ghapp/  "ghapp" — GitHub App auth (JWT, installation tokens), webhook signatures, REST client
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
uv run mypy packages/engine/src packages/db/src packages/ghapp/src apps/api/src apps/worker/src

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

## Tenant isolation (enforced by Postgres)

Every organisation's data is kept apart by the database itself, not only by `WHERE` clauses:

- Every tenant table (`repositories`, `pull_requests`, `analysis_runs`, `findings`,
  `branch_index`, `github_installations`, `ai_providers`, ...: the list is
  `db.tenancy.TENANT_TABLES`) carries `org_id` and a **row-level security** policy:
  `org_id = revu_current_org()`, read from the transaction-local setting `app.current_org`.
  A session bound to no organisation sees no tenant rows at all.
- Child rows can't drift from their parent: foreign keys are composite, `(repo_id, org_id) ->
  repositories(id, org_id)` and so on down the chain, and a trigger copies `org_id` from the
  parent when an insert leaves it out.
- The API, worker and webhook relay connect as **`revu_app`**: not a superuser, no BYPASSRLS, DML
  only. Migrations run as the schema owner. Both services check this at startup and **refuse to
  start in production** if their connection could bypass row-level security.
- The API binds each request's session to the signed-in user's organisation
  (`api.core.deps.get_current_user`). Worker jobs carry their organisation in their arguments
  and bind to it, so a run id paired with the wrong organisation is simply not found.
- Webhooks arrive before any organisation is known. They resolve it through the only
  cross-organisation lookup the database exposes, `revu_installation_org(installation_id)`, a
  `SECURITY DEFINER` function that returns that one mapping and nothing else.
- Repositories are unique **per organisation**: two organisations connecting the same GitHub
  repository get two fully separate copies (rows, clones, indexes, reviews).
- On disk, each repository's clone and signed index blobs live under `<org_id>/<repo_id>/`.

`apps/api/tests/test_tenant_isolation.py` proves it: it seeds two organisations, switches to
`revu_app` and runs deliberately unfiltered queries, inserts and updates against every tenant
table. A catalog test fails if a future table gains `org_id` without a policy.

## Connection lifecycle, data retention and audit

**Connections (GitHub App installations)** are `active`, `suspended` or `uninstalled`:

| Event | What happens |
|---|---|
| A repository is added to an installation | Connected (or *restored*, keeping its history, if it was disconnected) |
| Removed from the installation | Disconnected: inactive, `disconnected_at` starts the retention period |
| Installation suspended on GitHub | Repositories paused; data kept, no retention clock |
| Unsuspended | Repositories that are still connected resume; ones removed meanwhile stay disconnected |
| App uninstalled | The connection is kept as `uninstalled`; all its repositories are disconnected |

- **One repository, one connection.** If two installations of the same organisation can see a
  repository, the first keeps it until it is uninstalled.
- **Retention.** The worker's hourly `purge_expired_data` deletes every repository disconnected for
  more than `REVU_DISCONNECTED_RETENTION_DAYS` (default 30), one organisation at a time under
  row-level security. It removes the row (and with it every PR, run, finding and index), then
  the clone and signed index blobs, then uninstalled connections with nothing left. It also
  sweeps orphaned directories.
- **Delete data now.** Org owners/admins can delete an uninstalled connection's data, or a
  disconnected repository's, immediately: `DELETE /github/installations/{id}/data` or
  `DELETE /repos/{id}/data`. Still-connected data is refused (`409`), since GitHub would sync it
  straight back.
- **Audit log.** Logins (and failed logins), platform-admin grants, GitHub App creation, linking,
  syncs, suspensions, uninstalls, repositories connected and disconnected, settings changes,
  reviews and index builds requested, and every deletion are recorded with actor, target and
  details, in the same transaction as the change. `GET /audit` (owners/admins) lists them
  newest first with cursor paging. Entries outlive the data they describe.
- **Roles.** Repository settings, data deletion and the audit log need an organisation owner or
  admin. Creating the GitHub App needs a **platform admin**: every signup owns its own
  organisation, so "owner" can't be the bar for the App every organisation installs. Grant it
  with `REVU_PLATFORM_ADMIN_EMAILS` or
  `docker compose exec api python -m api.cli platform-admin grant you@example.com`.

## Tests that touch the database

`apps/api/tests/test_models_db.py` runs real round-trip tests (enum storage, cascades, unique
constraints) against Postgres rather than mocking the ORM. The test database is rebuilt once per
run by the **real Alembic migrations** (`db.testing.prepare_test_database`), so row-level
security, the `revu_app` role and the triggers exist in tests exactly as in production; API and
worker tests then connect as `revu_app` (`TEST_APP_DATABASE_URL`). It looks for
`TEST_DATABASE_URL` (default `postgresql+psycopg://revu:revu_dev_password@localhost:5434/revu_test`)
and skips DB-backed tests — not fails — if nothing is reachable there. One-time setup against the compose Postgres:

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

This is the **manual** path, where the caller supplies the diff directly. It predates the GitHub App
and stays as a fallback that needs no GitHub setup. For connected repositories, see
"Connecting GitHub (Stage 10)" below, where the server fetches the diff itself.

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

Repository names are unique per organisation: a second organisation posting the same
`repo_full_name` gets its own, isolated repository row and runs (see "Tenant isolation").

A pasted diff is always reviewed `diff_only` (no repository access needed). The deeper
`cross_file` review (Stage 7's tool-calling agent, verified through Stage 8's evidence checker)
reads the repository, so it is only available for GitHub-connected repos, where the worker owns
the checkout: `POST /repos/{id}/pulls/{number}/analysis` with `{"agent": "cross_file"}` (see
"Connecting GitHub (Stage 10)"). `POST /analysis` used to accept a `repo_path` on the worker's
filesystem for this; it was removed because it let any user point the worker at any directory,
including another tenant's clone. Unknown fields such as `repo_path` are now rejected with `422`.

## Branch memory (Stage 5)

`BranchIndex`/`IndexUpdateLog` have a real row lifecycle, wired end to end: trigger a build via
the API, the worker fetches the branch into the repository's own cache directory and does the
git/indexing work, results land in Postgres. Builds are for GitHub-connected repos:
`POST /repos/{id}/index`, and a push to the default branch keeps the index fresh (see "Connecting
GitHub (Stage 10)"). The Stage 5 manual endpoint, `POST /repos/index` with a caller-supplied
`repo_path`, was removed for the same reason as `POST /analysis`'s `repo_path`.

```bash
curl -s -X POST http://localhost:8000/repos/$REPO_ID/index \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" -d '{"branch_name":"main"}'

# Poll until the build finishes
curl -s http://localhost:8000/repos/$REPO_ID/branches/main/index -H "Authorization: Bearer $TOKEN"
```

A forced full rebuild reuses the same trigger endpoint with `"force_full": true` — there's no
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
survival, then threshold and cap. Plain importable library, one entry point — wired into the live
`agent="cross_file"` path of GitHub PR reviews (see "Connecting GitHub (Stage 10)"); `diff_only`
findings skip it since that path has no repository access to check citations against:

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

## Frontend (Stage 9 — complete)

`apps/web` (Next.js 16 App Router + TypeScript + Tailwind + shadcn/ui, per
`Product_Architecture_FullStack.md` §6) has:
- A working sign up / log in / log out flow against the real `/auth` endpoints.
- GitHub screens (Stage 10): connect accounts, browse repositories and their open PRs, review a PR
  with one click, and toggle auto-review per repo.
- A collapsed "Advanced" form that reviews a pasted diff.
- The PR analysis view the architecture doc calls "never cut" — a diff with
findings anchored inline at the lines they cite, plus each finding's full evidence trail. The access
token lives in memory only (never `localStorage`), recovered on page reload via a silent call to
`POST /auth/refresh`, which relies on the `HttpOnly` cookie the backend already sets — no new backend
work needed for the auth piece.

The app shell is a collapsible sidebar (`Ctrl`/`Cmd`+`B` to toggle, state persists across reloads) —
**Dashboard**, **Repositories** and **GitHub** are wired up. The remaining planned sections from the
architecture doc's §6 (Branch Memory, AI Providers, Usage & Budget, History) are shown disabled with a
"Soon" badge rather than hidden, so the full planned IA is visible without any dead links. A dark/light
toggle in the top bar (`next-themes`) applies everywhere.

```bash
cd apps/web
cp .env.local.example .env.local   # NEXT_PUBLIC_API_BASE_URL, for `npm run dev` outside Docker
npm install
npm run dev     # http://localhost:3000 — needs the API on :8000 (docker compose up -d api or `uv run uvicorn ...`)
npm run test    # Vitest + React Testing Library, 88 tests
npm run test:e2e # Playwright, real browser — see "Manual and automated UI testing" below
npm run build   # production build; also what `docker compose build web` runs
```

The dashboard's **Advanced → "Trigger a review"** form reviews a pasted diff (`diff_only`, the same as
`POST /analysis`) — **submitting it against a real backend makes a real, billed LLM call**, the same as `curl`-ing the endpoint directly, so don't trigger it against a
real API key without meaning to. Click into any triggered run to see the PR analysis view
(`/runs/[runId]`). The diff and PR details come from the API (stored with the run since Stage 10),
so the page works in any tab or after a reload. Runs created before Stage 10 fall back to what the
triggering tab kept in sessionStorage.

## Connecting GitHub (Stage 10)

revu connects to GitHub as a **GitHub App**. Once installed on an account, revu lists that account's
repositories, shows each repo's open PRs, and reviews a PR on demand: the server fetches the diff
(and, for `cross_file`, the code) itself, so nobody pastes anything. This is entirely optional. With
the `GITHUB_*` variables blank, the app runs exactly as before and the manual "paste a diff" flow
still works.

**Auto-review is off by default for every repository.** A review makes billed LLM calls, so reviewing
on every PR open or push is opt-in per repo (the **Auto-review** switch on the Repositories screen, or
`PATCH /repos/{id}` with `{"auto_review_enabled": true}`). Even when it's on:
- Draft PRs are skipped.
- A head commit that has already been reviewed is never reviewed twice.
- Auto-review always uses the cheaper `diff_only` reviewer. `cross_file` is always an explicit
  per-PR choice.

### 1. Create the GitHub App: one click, from inside revu

Open **GitHub** in the sidebar and click **Create GitHub App**. Optionally, name a GitHub
organization to own it.

1. revu prepares the App's settings and your browser carries them to GitHub. GitHub shows them for
   confirmation; you can rename the App there. The settings are:
   - Read-only access to code and pull requests.
   - The `pull_request` and `push` events.
   - The callback and redirect URLs.
   - "Request user authorization (OAuth) during installation".
   - A webhook pointed at a fresh smee.io channel.
2. Click **Create GitHub App** on GitHub. You're sent back to `/github/app-created`, which:
   - Exchanges GitHub's one-time code for the App's ID, private key, client secret and webhook
     secret.
   - Stores them **encrypted** (`CREDENTIAL_ENCRYPTION_KEY`) in the `github_app_credentials` table.
3. Click **Install on your repositories** (step 3 below).

Nothing is copied by hand. A signed, one-hour `state` ties the redirect to the user who started it,
so a forged or replayed redirect is refused. There is one App per deployment: once it exists, the
button is replaced by the connect flow. Only organization owners and admins can create it.

> **Keep `CREDENTIAL_ENCRYPTION_KEY` stable.** The stored credentials can't be decrypted with a
> different key, and revu refuses to store them under the shipped placeholder when
> `ENVIRONMENT=production`. Generate a real key with the command in `.env.example`.

**Alternative: environment variables.** An operator can instead register an App by hand on GitHub
and set `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_APP_PRIVATE_KEY_PATH` (or `..._PRIVATE_KEY`),
`GITHUB_WEBHOOK_SECRET`, `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`. These need the same
permissions, events, callback URL and OAuth-on-install setting as above. When complete, env vars
take precedence over a stored App. Docker Compose mounts `./.secrets` read-only at
`/run/revu-secrets` for the `.pem`.

### 2. Webhooks reach your laptop automatically

GitHub can't reach `localhost`, so the App's webhook URL is a smee.io channel. The
**`webhook-relay`** Compose service (`python -m api.webhook_relay`):
- Reads that channel from the database.
- Subscribes to it.
- Replays each delivery to `POST /github/webhook` with GitHub's original signature headers.

It idles until an App exists, and picks one up without a restart.

Every delivery is checked against the webhook secret (HMAC-SHA256, `X-Hub-Signature-256`) before its
body is parsed, so the relay is trusted with nothing. Deliveries are de-duplicated by
`X-GitHub-Delivery`, so GitHub's retries and manual redeliveries never queue a second review.

On a server with a public URL, set `GITHUB_WEBHOOK_PUBLIC_URL=https://<host>/github/webhook` before
creating the App. GitHub then posts directly and no smee channel is created.

### 3. Install the App and link it to your revu organization

Open **GitHub** in the sidebar and click **Connect GitHub**. This goes to the App's install page,
`https://github.com/apps/<slug>/installations/new`. After you pick repositories, GitHub redirects to
`http://localhost:3000/github/setup?code=...&installation_id=...`, and that page passes both values to
`POST /github/installations`. If your session had expired, you sign in first and return to the
same URL: the `?next=` return path is same-origin only. The server then:
1. Exchanges `code` for a user token.
2. Checks that the installation appears in that user's `GET /user/installations`. The
   `installation_id` in the URL alone is never trusted.
3. Syncs the installation's repositories.

You land on **Repositories**:
1. Every repo starts with **Auto-review off**.
2. Open a repo to see its open PRs (live from GitHub), each with revu's latest run.
3. Pick the review depth and click **Review**. Each review is one billed LLM call.
4. **Build index** (free) enables `cross_file`.
5. To add or remove repos, use **Manage on GitHub**. Then **Sync repos** on the GitHub screen, or
   just wait for the `installation_repositories` webhook.

### Endpoints

| Endpoint | What it does |
|---|---|
| `GET /github/app` | whether an App is set up (and from env or the database), plus the install URL |
| `POST /github/app/manifest` · `POST /github/app/conversions` | one-click App creation: start (manifest + signed state) and finish (one-time code → stored credentials) |
| `GET /github/installations` · `POST /github/installations` | list and link installations |
| `POST /github/installations/{id}/sync` | full re-sync of an installation's repositories |
| `POST /github/webhook` | GitHub → revu (signature-verified, unauthenticated by design) |
| `GET /repos` · `PATCH /repos/{id}` | list the org's repos; toggle `auto_review_enabled` |
| `GET /repos/{id}/pulls` | open PRs, live from GitHub, each with revu's latest run for it |
| `POST /repos/{id}/pulls/{number}/analysis` | review a PR (`{"agent": "diff_only" \| "cross_file"}`; `cross_file` needs a ready index → else 409) |
| `POST /repos/{id}/index` | build or refresh branch memory from GitHub (defaults to the repo's default branch) |

A push to the default branch refreshes its index automatically, but only for repos that were
indexed at least once.

## Manual and automated UI testing

### 1. Bring the stack up

```bash
docker compose up --build
```

Wait for all five containers to report healthy/running (`docker compose ps`), then open
**http://localhost:3000** — it redirects to `/login` since nothing is signed in yet.

### 2. Manually test it from the browser

1. On the login page, click **Sign up**.
2. Fill in an organization name, an email, and a password (min. 8 characters) — anything
   made up works, e.g. `Acme Inc` / `you@example.com` / `correct-horse-battery` — and submit.
3. You land on **/dashboard**, and the top bar shows the email you just signed up with.
4. Reload the page (F5). You stay on the dashboard — the session survives a reload because it's
   recovered from the backend's `HttpOnly` refresh cookie, not from anything readable by JavaScript.
5. Click **Sign out** in the top bar. You're returned to `/login`.
6. Try navigating directly to `http://localhost:3000/dashboard` while signed out — you're bounced
   back to `/login`, not shown a flash of the dashboard.
7. Log back in with the same email/password from step 2. You land back on `/dashboard`.
8. Try logging in with the right email but a wrong password — the form shows
   *"invalid email or password"* inline and does not navigate away.

9. In the sidebar, open **Repositories**. With no GitHub App configured, it says no repositories
   are connected and offers **Connect GitHub**. The **GitHub** screen explains that the App isn't
   configured yet. With an App configured, follow "Connecting GitHub" above.
10. On the dashboard, expand **Advanced: review a pasted diff** to reach the manual forms. They make a
    billed LLM call on submit.

Everything above talks to the real API (no mocks) — the org/user you create is a real row in the
dev Postgres database. Clean it up afterward if you like:

```bash
docker exec ai-code-reviewer-postgres-1 psql -U revu -d revu -c "DELETE FROM organizations WHERE name = 'Acme Inc';"
```

### 3. Or run the same walkthrough as an automated Playwright test

`apps/web/e2e/github.spec.ts` covers the Stage 10 screens on a stack without a GitHub App:
- The empty Repositories and GitHub states.
- The collapsed Advanced form.
- A signed-out GitHub callback, which must come back to `/github/setup` after login.
- An off-site `?next=`, which must be ignored.

`apps/web/e2e/auth.spec.ts` drives a real Chromium browser through exactly the steps above (sign up,
reload-survives-session, sign out, blocked-when-signed-out, log back in, wrong-password error) against
whichever stack is running at `http://localhost:3000` — it creates its own uniquely-named
organization per run and deletes it again in an `afterAll` hook, so it's safe to re-run repeatedly.

```bash
cd apps/web
npx playwright install chromium   # first time only
npm run test:e2e
```

Point it at a different stack (e.g. `npm run dev` on a non-default port) with
`PLAYWRIGHT_BASE_URL=http://localhost:3001 npm run test:e2e`.

## Status

Build order and what's covered vs. deferred from the original roadmap: see
[`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md). For copy-pasteable steps to independently
verify what's already built, see [`VERIFICATION.md`](VERIFICATION.md).
