# revu: Project Guide

How to run revu, how the codebase is laid out, and how to demonstrate every page.

revu is an agentic code review platform: it reviews pull requests using a real dependency graph of
the repository, not just the diff text. It is the implementation behind the M.Tech dissertation
*"Design and Development of an LLM-Based Agentic Code Review System with Repository-Level
Context"* (BITS Pilani, 2024TM93654).

**Contents**

1. [What you need](#1-what-you-need)
2. [Running the application](#2-running-the-application)
3. [How the system fits together](#3-how-the-system-fits-together)
4. [Folder structure](#4-folder-structure)
5. [Page-by-page demo guide](#5-page-by-page-demo-guide)
6. [Suggested demo flow](#6-suggested-demo-flow-about-12-minutes)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. What you need

| Tool | Version used | Needed for |
|---|---|---|
| **Docker Desktop** (with Docker Compose) | 29.x | Running everything: database, queue, API, worker, web app, webhook relay |
| **Git** | any recent | Cloning; the worker also uses Git internally |
| **uv** (Python package manager) | 0.12+ | Only for running tests or the backend outside Docker. Installs Python 3.12 itself |
| **Node.js** | 22+ | Only for frontend work or browser tests outside Docker |
| An **Anthropic or OpenAI API key** | | Only for running real reviews (each review is a paid LLM call) |
| A **GitHub account** | | Only for the GitHub integration (connecting repositories) |

Free ports needed on your machine: **3000** (web app), **8000** (API), **5434** (Postgres),
**6380** (Redis).

Everything except running a review is free. Logging in, browsing pages, connecting GitHub,
toggling settings and building a code index all cost nothing.

---

## 2. Running the application

### First time

```bash
# 1. Get the code
git clone https://github.com/Vijay-Pratap-Singh-Parihar/ai-code-reviewer.git
cd ai-code-reviewer

# 2. Create your settings file from the template
cp .env.example .env
```

Open `.env` and set:

| Variable | What to put | Required? |
|---|---|---|
| `JWT_SECRET_KEY` | Any long random string (command is in the file's comments) | Yes, before using it anywhere but your laptop |
| `CREDENTIAL_ENCRYPTION_KEY` | A Fernet key (command is in the file's comments). **Never change it afterwards**: it encrypts the stored GitHub App credentials | Yes, before using it anywhere but your laptop |
| `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` | Your LLM key | Only to run real reviews |
| `GITHUB_*` | Leave blank | No. The GitHub App is created from inside the app with one click |

```bash
# 3. Build and start all six services
docker compose up -d --build

# 4. Create the database tables (first run, and after pulling new code)
docker compose exec api alembic upgrade head

# 5. Check that everything is up
docker compose ps                    # six services, all "Up"
curl http://localhost:8000/health    # {"status":"ok"}
```

Then open **http://localhost:3000**.

### Every day after that

```bash
docker compose up -d        # start
docker compose stop         # stop (keeps all data)
```

After pulling new code: `docker compose up -d --build`, then
`docker compose exec api alembic upgrade head`.

> **Do not run `docker compose down -v`** unless you want a clean slate. The `-v` deletes the
> database volume: every account, review, repository and the GitHub App connection.

### What is running

| Service | Address | What it does |
|---|---|---|
| `web` | http://localhost:3000 | The Next.js web app people use |
| `api` | http://localhost:8000 (docs at **/docs**) | FastAPI backend: auth, reviews, repositories, GitHub |
| `worker` | (no port) | Background jobs: runs reviews, builds code indexes, talks to GitHub |
| `postgres` | localhost:5434 | The database |
| `redis` | localhost:6380 | The job queue between API and worker |
| `webhook-relay` | (no port) | Forwards GitHub webhooks from smee.io to the API in local development |

### Running tests (optional)

```bash
uv sync --all-packages --group dev
docker exec ai-code-reviewer-postgres-1 psql -U revu -d revu -c "CREATE DATABASE revu_test"  # once
uv run pytest                          # 352 backend tests
cd apps/web && npm install
npm run test                           # 94 frontend unit tests
npx playwright install chromium        # once
npm run test:e2e                       # 6 real-browser tests (needs the stack running)
```

---

## 3. How the system fits together

```
 Browser ──> web (Next.js, :3000) ──HTTP──> api (FastAPI, :8000) ──> Postgres
                                               │  enqueue job
                                               ▼
                                            Redis queue
                                               │
                                               ▼
                                            worker ──> review engine (packages/engine)
                                               │          indexer · context retrieval
                                               │          review agents · verifier
                                               ├──> LLM provider (paid calls)
                                               └──> GitHub API (diffs, code, repos)

 GitHub ──webhook──> smee.io ──> webhook-relay ──> api /github/webhook
```

**The review pipeline** (what happens when a PR is reviewed):

1. **Indexer** turns the repository's Python code into a graph: which function calls which,
   which module imports which.
2. **Branch memory** stores that graph per branch and updates it incrementally on each push.
3. **Change impact** maps the diff onto the graph and walks outward to find the code the change
   can actually affect, then fits it into a fixed token budget.
4. **Review tier**: `diff_only` (one cheap LLM call over the diff) or `cross_file` (an agent that
   can read files, find definitions and find callers before answering, about 4x the cost).
5. **Verifier** merges duplicate findings and drops any finding whose cited file and line do not
   exist, which is how fabricated citations are caught.

---

## 4. Folder structure

```
ai-code-reviewer/
├── apps/                      Deployable applications
│   ├── api/                   FastAPI backend (HTTP API)
│   ├── worker/                Background job runner (reviews, indexing, GitHub)
│   └── web/                   Next.js frontend (the website)
├── packages/                  Shared Python libraries used by api and worker
│   ├── engine/                "revu": the review engine (the research core)
│   ├── db/                    Database models shared by api and worker
│   └── ghapp/                 GitHub App integration (auth, webhooks, API client)
├── docs/                      This guide
├── .github/workflows/ci.yml   CI on every push: backend lint, type checks and tests; web lint and build
├── docker-compose.yml         Defines the six services
├── pyproject.toml, uv.lock    Python workspace (all Python packages are installed together)
├── .env.example               Template for configuration and secrets
├── README.md                  Setup and feature reference
├── IMPLEMENTATION_PLAN.md     Stage-by-stage build log: what was built, why, and how it was verified
├── VERIFICATION.md            Copy-paste steps to independently verify each stage
├── Product_Architecture_FullStack.md   Original product and architecture design
└── Execution_Roadmap_Weeks_0_to_18.md  Original research roadmap
```

**Why this shape:** the API and the worker never import each other. Both depend on the shared
packages, so the engine and the data model are written once. The engine has no idea it is
inside a web application, which keeps it testable and reusable for the research evaluation.

### `packages/engine/src/revu/`: the review engine

| Folder / file | What it holds |
|---|---|
| `models.py` | Core data shapes: `Finding` (file, lines, category, severity, message, evidence, confidence), `EvidenceItem`, `ContextBundle`, `RunResult` |
| `index/` | **Repository indexer.** `walker.py` finds source files, `symbols.py` extracts functions and classes with tree-sitter, `imports.py` and `calls.py` resolve imports and calls, `graph.py` builds the graph, `incremental.py` updates it per commit, `vcs.py` and `checkout.py` handle Git (fast-forward checks, merge base, temporary worktrees), `store.py` saves graphs to disk |
| `context/` | **Change impact and retrieval.** `diff.py` parses diffs, `mapping.py` maps changed lines to graph nodes, `traverse.py` walks the graph outward, `rank.py` orders candidates, `budget.py` fits them into a token budget, `bundle.py` assembles the final context |
| `agents/` | **Review tiers.** `diff_only.py` is the single-call reviewer; `cross_file.py` is the LangGraph tool-using agent |
| `agents/tools/` | The agent's tools: `read_file`, `find_definition`, `find_callers`, `graph_query` |
| `verify/` | **Verifier.** `dedup.py` merges duplicates, `evidence.py` checks cited locations exist, `confidence.py` rescores, `rank.py` orders and caps findings |
| `providers/llm.py` | One interface to every LLM provider (via LiteLLM), with token and cost accounting |

### `packages/db/src/db/`: database models

| File | Tables |
|---|---|
| `organization.py` | Organizations, users, GitHub installations |
| `auth.py` | Refresh tokens (for login sessions) |
| `repository.py` | Repositories (including the per-repo **auto-review** switch) and tracked branches |
| `pull_request.py` | Pull requests, review runs (with the reviewed diff), findings, context bundles |
| `branch_index.py` | Branch memory: one row per index build, plus an update log |
| `github.py` | The GitHub App's encrypted credentials, and processed webhook deliveries |
| `provider.py`, `ledger.py` | AI provider settings and token-usage ledger (screens come in Stage 11) |
| `base.py`, `mixins.py`, `_enum.py` | Shared base class, ID and timestamp columns, enum handling |

### `packages/ghapp/src/ghapp/`: GitHub integration

| File | What it does |
|---|---|
| `auth.py` | Signs the App's short-lived JWT with its private key |
| `client.py` | Calls the GitHub API: installation tokens, repositories, PRs, diffs, OAuth |
| `webhooks.py` | Verifies webhook signatures (HMAC-SHA256) |
| `manifest.py` | Builds the "Create GitHub App" manifest (one-click setup) |
| `crypto.py` | Encrypts App credentials before they are stored |
| `relay.py` | Parses smee.io events for the webhook relay |

### `apps/api/`: the backend

| Path | What it holds |
|---|---|
| `src/api/main.py` | Creates the FastAPI app and plugs in the routers |
| `src/api/routers/` | HTTP endpoints: `auth.py` (signup, login, refresh, logout), `analysis.py` (start and read reviews), `branch_index.py` and `repos.py` (repositories, PRs, indexes), `github.py` (App setup, installations, webhook), `health.py` |
| `src/api/services/` | The logic behind those endpoints (routers stay thin) |
| `src/api/schemas/` | Request and response shapes (Pydantic) |
| `src/api/core/` | Settings (`config.py`), password and token security, the queue connection, the current-user dependency, GitHub App resolution |
| `src/api/webhook_relay.py` | The `webhook-relay` service's program |
| `alembic/versions/` | Database migrations, in order |
| `tests/` | API tests against a real Postgres |

### `apps/worker/`: background jobs

| Path | What it holds |
|---|---|
| `src/worker/settings.py` | Registers the jobs and connects to the database |
| `src/worker/jobs/analyze.py` | Runs a review (`diff_only` or `cross_file`) and saves findings |
| `src/worker/jobs/index_branch.py` | Builds or incrementally updates a branch's code graph |
| `src/worker/jobs/github.py` | GitHub-connected versions: fetch the PR diff and code, then review or index |
| `src/worker/repo_cache.py` | Local Git copies of connected repos (the access token never touches disk) |
| `tests/` | Worker tests against real Postgres and real Git repositories |

### `apps/web/`: the frontend

| Path | What it holds |
|---|---|
| `src/app/login`, `src/app/signup` | Login and sign-up pages |
| `src/app/(protected)/` | Every page that needs a signed-in user: `dashboard`, `runs/[runId]`, `repositories`, `repositories/[repoId]`, `github`, `github/setup`, `github/app-created` |
| `src/components/` | Page building blocks: the app shell and sidebar, the diff viewer, the evidence trail, forms, the repository and PR lists, the GitHub screens |
| `src/components/ui/` | Base UI components (buttons, cards, inputs, switch, sidebar) from shadcn/ui |
| `src/lib/api-client.ts` | Every call the frontend makes to the API |
| `src/lib/auth-store.ts` | Holds the login token **in memory only** (never in browser storage) |
| `src/lib/` (others) | Diff helpers, safe post-login redirects, badge colours |
| `e2e/` | Playwright tests that drive a real browser |

---

## 5. Page-by-page demo guide

For each page: **where it is**, **what to click**, and **what to say**. Talking points are written
so you can say them nearly word for word.

> **Before a demo:** start the stack, and sign in once to check it works. Have one finished review
> ready to open, so you never need to spend money during the demo. On the development machine, the
> review captured for the progress deck is at
> `http://localhost:3000/runs/dd19b2e8-807a-44f7-ace7-2eaa8d29c431`. That link only works on the
> machine where the run was made, signed in as its owner.

### 5.1 Sign up and log in (`/signup`, `/login`)

**Show:**
1. Open http://localhost:3000. You are redirected to `/login`.
2. Sign in. You land on the dashboard and your email appears in the top bar.
3. Press **F5**. You stay signed in.

**Say:**
- "Every organization's data is isolated. One company can never see another's repositories or
  reviews, and that is enforced in the database queries, not just in the UI."
- "The login token is kept only in memory, never in browser storage, so a malicious script could
  not steal it. The session survives a refresh through a secure cookie that JavaScript cannot
  read."
- "If someone replays a stolen refresh token, every session for that user is revoked."

### 5.2 The app shell (sidebar, top bar, dark mode)

**Show:**
1. The sidebar: **Dashboard**, **Repositories** and **GitHub** work. **Branch Memory**, **AI
   Providers**, **Usage & Budget** and **History** are marked *Soon*.
2. Collapse the sidebar with the icon at the top left (or **Ctrl+B**).
3. Click the sun/moon icon to switch dark and light mode.

**Say:**
- "The planned sections are shown on purpose, so you can see the full product scope: they are
  the next stage, not missing features."

### 5.3 Dashboard (`/dashboard`)

**Show:**
1. **Session analytics**: runs, succeeded, failed, in progress, findings and cost for this
   session.
2. **Repositories**: connected GitHub repositories, or a **Connect GitHub** button if none are
   connected yet.
3. **Advanced: review a pasted diff**: click to expand. This is the manual way to run a review
   without GitHub.

**Say:**
- "The main path is GitHub: connect once, then review pull requests with one click. The
  paste-a-diff form is kept for reviewing changes that are not on GitHub."
- "Cost is visible on every run. Reviews are real paid LLM calls, so nothing runs without
  someone choosing it."

> **Cost note:** submitting the Advanced form runs a real review (well under one cent with
> `diff_only`). Expanding the section is free.

### 5.4 PR analysis view (`/runs/<id>`): the key screen

Open it from a run card on the dashboard, from a PR on a repository page, or from a saved link.

**Show:**
1. The header: PR title, status, repository, branch, PR number and review tier, plus tokens,
   cost and time taken.
2. **Diff**: the change with removed lines in red and added lines in green. **Findings appear
   inline on the lines they refer to.**
3. **Findings & evidence trail**: each finding with its severity, location, confidence and
   explanation. For `cross_file` reviews, it also lists the files the agent looked at and why.

**Say:**
- "This is the screen that separates revu from a plain chatbot wrapper. Every finding points to an
  exact file and line, and shows its evidence."
- "In this example, the reviewer caught an off-by-one bug that would crash with an IndexError
  (critical, confidence 0.98), and a missing check for negative discounts (medium, 0.70). The
  whole review cost less than half a cent."
- "Before findings are saved, a separate verifier stage checks that every cited location really
  exists. Language models sometimes invent references; in our controlled test, the verifier
  removed both fabricated citations and kept the real one."
- "The deeper `cross_file` tier can follow the call graph: on a real cross-file bug, it named the
  exact broken caller at confidence 0.98, while the diff-only reviewer produced three vague
  guesses."

### 5.5 GitHub (`/github`)

**Show:**
1. If no GitHub App exists yet: the **Set up GitHub** card with **Create GitHub App**.
2. Once it exists: the **Connect GitHub** button, the App's name, and the note that webhooks are
   relayed through smee.io.
3. **Connected accounts**: each connected GitHub account with its repository count, **Sync
   repos** and **Manage on GitHub**.

**Say:**
- "Setting up GitHub takes one click. revu prepares the GitHub App's settings, GitHub shows them
  for confirmation, and the credentials come back to revu automatically and are stored
  encrypted. Nobody copies keys by hand."
- "The App only asks for read access to code and pull requests."
- "When someone installs it, we verify with GitHub that this person really owns that
  installation, so nobody can attach someone else's repositories by guessing an ID."

**Live (optional, free):** click **Connect GitHub**, pick a test repository on GitHub, and you are
brought back to **Repositories** with it listed.

### 5.6 GitHub callback pages (`/github/app-created`, `/github/setup`)

You never open these yourself: GitHub sends you here after **Create GitHub App** and after
installing. They finish the setup and move you on (to "Install on your repositories", and then to
**Repositories**).

**Say (if asked):** "If your session had expired in the meantime, you sign in and are brought
straight back here, so the one-time code from GitHub is not lost."

### 5.7 Repositories (`/repositories`)

**Show:**
1. Every repository from connected GitHub accounts, with its default branch.
2. The **Auto-review** switch on each repository, **off** by default.
3. Badges: **manual** means the repository came from the paste-a-diff form; **access removed**
   means the GitHub App was uninstalled or lost access.

**Say:**
- "Auto-review is off by default for every repository, because each review costs money. When you
  turn it on, every new pull request and every new push to it is reviewed automatically. Draft
  PRs are skipped, and the same commit is never reviewed twice."
- "Automatic reviews always use the cheaper diff-only tier. The deeper review is a deliberate
  choice per PR."

Toggling the switch is free; it only spends money when a PR event arrives for that repository.

### 5.8 Repository detail (`/repositories/<id>`)

**Show:**
1. **Automatic review** card: the switch and what "on" means.
2. **Branch memory** card: the code-graph status for the default branch, with node and edge
   counts, and a **Build index** button. Building is free and refreshes automatically on each
   push once built.
3. **Open pull requests**: live from GitHub, each with revu's latest review and whether it covers
   the latest commit.
4. **Review depth** picker and the **Review** button. `cross_file` stays disabled until the index
   is ready.

**Say:**
- "Indexing is the expensive part, so it is done once per branch and then updated incrementally:
  a 45,000-line codebase indexes in 1.45 seconds from cold and updates in 0.36 seconds per
  commit."
- "The pull request list comes live from GitHub. One click reviews a PR; nobody pastes a diff."

> **Cost note:** **Review** makes a real LLM call. In a demo, open an existing review instead,
> unless spending a fraction of a cent is fine.

### 5.9 API documentation (http://localhost:8000/docs)

**Show:** the interactive list of every API route (21 paths), grouped by area. Each can be tried
from the page.

**Say:** "Everything the web app does goes through this documented API, so the review engine can
also be driven by CI pipelines or other tools."

---

## 6. Suggested demo flow (about 12 minutes)

| Time | Page | Purpose |
|---|---|---|
| 1 min | Login and refresh (5.1) | Real authentication; the session survives a refresh |
| 1 min | Shell, sidebar, dark mode (5.2) | Product scope at a glance |
| 2 min | Dashboard (5.3) | Analytics, the GitHub path, the manual path |
| 4 min | **PR analysis view (5.4)** | The core result: inline findings with evidence |
| 2 min | GitHub (5.5) | One-click integration, security |
| 2 min | Repositories and detail (5.7, 5.8) | Auto-review off by default, branch memory, live PRs |
| | API docs (5.9) | Only if there is time or someone asks |

**Key numbers to remember:** 1.45 s cold index (45k lines), 0.36 s per-commit update, 10/10
verified cross-file call links, 2/2 fabricated citations removed, about 4x cost for `cross_file`,
452 automated tests (352 backend, 94 web, 6 browser).

**What not to do live:** do not click **Review** on a PR or submit the Advanced form unless you
mean to spend money; do not run `docker compose down -v`.

---

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| Page shows "Failed to load" or login fails | `docker compose ps`: all six services should be Up. `docker compose logs api` shows errors |
| Errors mentioning a missing table or column | Run `docker compose exec api alembic upgrade head` |
| A review stays "queued" | The worker is not running or has no LLM key: `docker compose logs worker` |
| A review fails immediately | Check the LLM key in `.env`, then `docker compose up -d worker` |
| GitHub page says the App cannot be used | `CREDENTIAL_ENCRYPTION_KEY` was changed after the App was created. Restore the old key |
| Pull requests or webhooks not arriving | `docker compose logs webhook-relay` should show `relaying https://smee.io/...` |
| Port already in use | Another program is using 3000, 8000, 5434 or 6380. Stop it, or change `HOST_POSTGRES_PORT` and `HOST_REDIS_PORT` in `.env` |
| Session analytics show zero | Expected after a page reload or a new sign-in: they count the current browser session only. Past reviews still open from their links. Persistent history is Stage 11 |

More detail: `README.md` (setup and every feature), `VERIFICATION.md` (step-by-step checks),
`IMPLEMENTATION_PLAN.md` (what was built in each stage and why).
