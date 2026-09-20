# Product Architecture — Full-Stack Agentic Code Review Platform

This document covers the application layer. The review engine beneath it is unchanged from the build plan; this describes how it is wrapped, operated and consumed.

---

## 1. Scope verdict, stated plainly

What you have described is a multi-tenant SaaS product. Building it well takes a small team six to twelve months. You have one semester, and the marks come from the evaluation, not the engineering.

**Therefore:**

| Weeks | What you build | Why |
|---|---|---|
| 1–13 | Engine, CLI, benchmark harness (Build Plan Phases 0–6) | This is the dissertation. Numbers locked by week 13. |
| 14–18 | The application, as the delivery surface | This is the demo, the deployment story, and the source of one supplementary result. |
| 19–20 | Writing | — |

If you run out of time, you ship the engine with a CLI and a thin dashboard, and you still have a dissertation. If you invert the order, you ship a beautiful application with an empty results chapter and no degree.

**The application is not wasted effort academically.** It buys you two things the CLI cannot: a real deployment on live repositories, which is what ICSE-SEIP-style work is built on, and the ability to measure **Outdated Rate** — whether developers actually acted on your comments. That is a genuine adoption metric, and it addresses the strongest criticism in the literature: that AI review comments are fluent but unadopted, at 16.6% against 56.5% for humans.

---

## 2. Branch memory — your strongest idea, done properly

You arrived at this independently and it is the single most valuable architectural instinct in your message. It deserves to be the product's defining feature.

### Why it matters

Indexing a repository is expensive. Analysing a diff is cheap. Every existing tool conflates the two and pays the indexing cost on every pull request. If the code graph for `main` is built once and then *incrementally updated on each merge*, the marginal cost of analysing a PR collapses to the cost of the diff plus a bounded traversal.

This is simultaneously:

- **A product differentiator.** It is why your tool is faster and cheaper than competitors.
- **A thesis result.** Cold index time versus warm update time is a reportable efficiency number, and it is exactly what the literature says is missing — the cost dimension of repository context.
- **Already in your build plan.** Phase 3 step 10 is incremental update. You are promoting it from an implementation detail to a product primitive.

### The design

Store one index snapshot per `(repository, base_branch)` pair, versioned by commit SHA.

```
branch_index(
  id, repo_id, branch_name, head_sha,
  graph_blob | graph_tables, node_count, edge_count,
  built_at, updated_at, build_duration_ms, status
)
```

On a `push` event to a tracked base branch: compute the changed file set between the stored `head_sha` and the new one, reparse only those files, patch the affected subgraph, advance `head_sha`. On a `pull_request` event: load the snapshot, compute the blast radius, analyse.

### Four subtleties you have not accounted for

These will bite you, so design for them now.

**Merge-base, not head.** A PR's context must be computed against the base branch as it stood at the PR's *merge base*, not against the latest `main`. If `main` has moved twelve commits since the PR branched, analysing against current `main` gives you findings about code the author never saw. Store the merge-base SHA on the analysis run and, where the snapshot has drifted, either walk the index backwards or note the drift in the run record.

**Force-push and rebase invalidate the delta.** If a tracked branch's history is rewritten, your incremental delta is meaningless. Detect non-fast-forward updates and trigger a full rebuild. Log rebuild frequency — it is a real operational cost.

**Unresolved symbols must be stored.** Your import resolver will fail on some references. Persist those failures with the index rather than discarding them, so that a later merge that adds the missing module can resolve them retrospectively. Also gives you a quality metric per repository.

**Index staleness is a first-class state.** A PR may arrive while the base index is mid-rebuild. The run must either wait, or proceed against the previous snapshot and record that it did. Never analyse against a partially-written graph.

### The feature you get for free

Because you hold a graph per base branch and update it on merge, you can answer a question no diff-based tool can: **what changed in the repository's structure between two points in time?** New public APIs, removed functions still referenced elsewhere, growth in coupling. That is a genuinely novel product surface and a strong viva demo.

---

## 3. Token and cost control

You flagged this correctly — without it the system will exhaust budgets. Six mechanisms, in order of impact.

**1. Triage before inference — the biggest saver.** The cheapest token is the one never spent. Before any LLM call, drop: lockfiles, generated code, vendored directories, minified assets, pure-whitespace or pure-formatting diffs, and files matching per-repo ignore globs. On a typical PR this removes a large fraction of the diff at zero cost.

**2. Hard per-PR token ceiling.** Your Phase 4 knapsack already enforces a context budget. Extend it to a total-run ceiling covering context, agent prompts and agent outputs. Configurable per repository.

**3. Pre-flight estimate with a gate.** Before running, compute the estimated token count and cost from the triaged diff and the planned context bundle. Show it in the UI. If it exceeds the repo threshold, require confirmation rather than silently spending. This alone prevents the runaway case where someone opens a 4,000-file refactor PR.

**4. Content-addressed result cache.** Key on `hash(diff_hunk, context_bundle, model, prompt_version)`. Re-runs after a force-push that touched three files should not re-analyse the other forty. Cache hit rate is a reportable efficiency number.

**5. Tiered model routing.** A cheap model screens every hunk for risk; the expensive model runs only on hunks it flags, or on hunks whose blast radius exceeds a threshold. This is also a clean ablation: does tiering cost accuracy?

**6. Output caps.** The verifier's comment cap bounds output tokens, and it is independently justified — concise, hunk-level, snippet-rich comments are the ones that actually cause code changes.

**Quota enforcement** sits above all of this: per-organisation monthly token budget, tracked in Postgres, checked at the budget gate, with a soft warning threshold and a hard stop. Record actual consumption per run so the UI can show real spend, not estimates.

---

## 4. Stack decisions

### Backend: FastAPI

You asked for "fastest and best." For this system that is **FastAPI**, and the reasoning is not about raw HTTP throughput.

Your engine is Python — tree-sitter bindings, the benchmark harness, the metric implementations, the statistics. If you write the API in Go or Rust, you still need a Python worker, so you have bought yourself a serialisation boundary, two deployment artifacts, two dependency systems and two debugging contexts, in exchange for HTTP throughput you will never approach. Your latency budget is dominated by LLM inference at two to twenty seconds per call. Framework overhead is noise.

| Layer | Choice | Note |
|---|---|---|
| API | **FastAPI** + Uvicorn | Async, Pydantic-native — your `Finding` models become API schemas for free |
| Worker | **ARQ** (or Dramatiq) + Redis | Lighter than Celery. Analysis is minutes-long; it must be a background job |
| Database | **PostgreSQL 16** + SQLAlchemy 2.0 + Alembic | — |
| Graph storage | Postgres tables, or a serialised `rustworkx` blob per snapshot | Benchmark both; blob is simpler, tables allow SQL queries over structure |
| Cache / queue | **Redis** | Also holds the content-addressed result cache |
| Realtime | **SSE** over WebSockets | Analysis progress is one-directional; SSE is simpler and survives proxies better |
| Auth | JWT access (15 min) + rotating refresh (httpOnly cookie) | Never put the access token in `localStorage` |
| Secrets | **Envelope encryption** for provider API keys | A plain Postgres column is a serious flaw an examiner may well probe |
| Frontend | **Next.js 15** App Router + TypeScript + Tailwind + shadcn/ui | — |
| Diff viewer | `react-diff-view` or `diff2html` | Do not write your own |
| Graph viz | `react-flow` or `cytoscape.js` | For the blast-radius view |

### GitHub integration: a GitHub App, not OAuth tokens

This matters more than it sounds. A GitHub App gives you installation-scoped permissions, short-lived tokens, webhook delivery, per-repository access control and a proper permissions dialogue. Personal access tokens give you a long-lived credential with far too much scope, stored in your database. Use the App.

Webhooks you need: `pull_request` (opened, synchronize, reopened, closed), `push` (base-branch index updates), `issue_comment` (for `/review` re-trigger), `installation` (lifecycle).

---

## 5. Data model

The tables that matter. Multi-tenancy by `org_id` throughout, enforced at the query layer.

```
organizations       id, name, plan, token_quota_monthly, token_used_current_period
users               id, org_id, email, password_hash, role, created_at
github_installations id, org_id, installation_id, account_login, permissions, installed_at

repositories        id, org_id, installation_id, full_name, default_branch,
                    languages, is_active, config_json
tracked_branches    id, repo_id, branch_name, is_protected, auto_index

branch_index        id, repo_id, branch_name, head_sha, node_count, edge_count,
                    graph_ref, unresolved_symbols, status, built_at, updated_at,
                    build_duration_ms
index_update_log    id, branch_index_id, from_sha, to_sha, files_changed,
                    mode(incremental|full), duration_ms, reason

ai_providers        id, org_id, kind(anthropic|openai|bedrock|azure|vertex),
                    encrypted_credentials, key_version, is_default, verified_at
model_routes        id, org_id, tier(screen|review|verify), provider_id, model_name

pull_requests       id, repo_id, number, title, body, author, base_branch, head_sha,
                    merge_base_sha, state, opened_at, merged_at
analysis_runs       id, pr_id, branch_index_id, config_snapshot, status,
                    tokens_in, tokens_out, cost_usd, latency_ms,
                    estimated_tokens, cache_hits, started_at, finished_at, error
findings            id, run_id, file_path, line_start, line_end, category, severity,
                    message, evidence_json, confidence, agent_name,
                    posted_comment_id, developer_action(accepted|dismissed|ignored),
                    resolved_at
context_bundles     id, run_id, items_json, total_tokens, retrieval_strategy

token_usage_ledger  id, org_id, run_id, tokens_in, tokens_out, cost_usd, occurred_at
audit_log           id, org_id, actor_id, action, target, metadata, at
```

**Note `findings.developer_action`.** That column is how you compute Outdated Rate. Populate it from GitHub comment reaction and resolution webhooks. It is the bridge between your product and a publishable adoption result — design it in now, not later.

---

## 6. Application sections

| Section | Contents | Priority |
|---|---|---|
| **Auth** | Login, signup, refresh rotation, logout | P0 |
| **Onboarding** | Install GitHub App, pick org, select repositories, choose base branches, add first AI provider, trigger first index | P0 |
| **Dashboard** | Open PRs awaiting analysis, recent runs with status, token spend this cycle against quota, index health across repos | P0 |
| **PR Analysis view** | Split diff viewer with findings inline; per-finding evidence trail showing which files were retrieved and why; blast-radius graph; accept/dismiss; post to GitHub; re-run | P0 — this is your viva demo |
| **Repositories** | Per-repo config: tracked base branches, ignore globs, severity threshold, comment cap, token ceiling, enabled agents | P1 |
| **Branch Memory** | Index freshness per branch, node/edge counts, last update mode and duration, unresolved-symbol rate, manual rebuild | P1 — showcases the differentiator |
| **AI Providers** | Add credentials per provider, test connection, model routing per tier, cost-per-token reference | P1 |
| **Usage and Budget** | Token and cost breakdown by repo and by run, quota configuration, pre-flight estimates, cache hit rate | P1 |
| **History** | All past runs including merged PRs, filterable by repo, severity, outcome; adoption statistics | P1 |
| **Organisation** | Members, roles, audit log | P2 |

**Design reference.** Linear and GitHub's own PR interface are the right models — dense, keyboard-navigable, low-chrome. Avoid marketing-style dashboards with large cards and sparse data. Developers judge review tools by information density and speed.

**The single screen that matters most** is the PR Analysis view showing the evidence trail: this finding was produced because these three files were retrieved by traversing these graph edges. No competing tool shows that, it is a direct visualisation of your research contribution, and it is what you put on screen at the viva.

---

## 7. Provider abstraction

Route everything through **LiteLLM** rather than five SDKs. One interface across Anthropic, OpenAI, Bedrock, Azure and Vertex, with per-provider credential shapes handled at the adapter boundary.

Three things to get right:

**Normalise token accounting.** Providers report usage differently and price differently. Convert to a common `(tokens_in, tokens_out, cost_usd)` record at ingestion so your ledger is comparable across providers.

**Verify on save.** When a user adds credentials, make a trivial call and store `verified_at`. Silent credential failure at 3am inside a webhook handler is a bad first experience.

**Route by tier, not by task.** `screen`, `review`, `verify` — let the user map each tier to a provider and model. This makes your tiered-routing cost optimisation a user-facing feature rather than a hidden heuristic, and it gives you a clean experimental knob.

---

## 8. How the research maps onto the product

Each feature below traces to a specific finding. This mapping is worth putting in your thesis — it demonstrates that the design is evidence-driven rather than assembled from intuition.

| Finding in the literature | Product feature |
|---|---|
| Bulk repository context gives no gain and costs 20% more | Token-budgeted context bundle; no whole-repo dumps |
| Agentic exploration: high precision, very low recall | Graph traversal instead of lexical search; context recall measured directly |
| Agents suffer exploration drift | Structure built ahead of time in branch memory, not explored per-run |
| False positives lengthened PR closure time despite 73.8% resolution | Evidence gate, confidence threshold, comment cap |
| Precision reached ~75% only with a dedicated filter stage | Verifier as a distinct pipeline stage, independently measurable |
| Multi-review aggregation lifted F1 by up to 43.67% | Agent fan-out with aggregation |
| Comments causing change are concise, hunk-level, snippet-rich, manually re-triggerable | Output format constraints; `/review` slash command |
| AI suggestions adopted at 16.6% vs 56.5% human | `developer_action` tracking; Outdated Rate surfaced in the UI |
| Generic style advice is the classic irrelevant-comment failure | Conventions mined from repository git history |

---

## 9. Build order for the application layer

Weeks 14–18, after the engine numbers are locked.

1. **Week 14** — Postgres schema, Alembic migrations, FastAPI skeleton, JWT auth, ARQ worker, health checks. Wrap the existing engine as a job, with no UI. Verify end to end via `curl`.
2. **Week 15** — GitHub App registration, webhook receiver with signature verification, installation flow, repository selection, first index build triggered from an API call.
3. **Week 16** — Branch memory: snapshot storage, incremental update on push, merge-base resolution, staleness states. Budget gate and token ledger.
4. **Week 17** — Next.js: auth flow, dashboard, PR analysis view with diff and evidence trail. This is the week that produces your demo.
5. **Week 18** — Providers screen, usage screen, branch memory screen, comment posting to GitHub, polish. Deploy and run on two or three live repositories.

**Cut order if you fall behind:** organisation/roles, audit log, usage screen, providers screen (hardcode one provider), history screen. Never cut the PR analysis view with the evidence trail — it is the demo.

---

## 10. Honest assessment

**What is genuinely strong here.** Branch memory with incremental merge-time update is a real architectural idea with both product and research value, and it is not what existing tools do. The evidence-trail UI makes an invisible contribution visible. Provider-agnostic routing with tiered models is a sensible cost design. Tracking developer action on findings connects your system to the adoption question the literature says is unresolved.

**What will consume time without earning marks.** Multi-tenancy, roles and permissions, audit logging, billing, onboarding polish, responsive design. Necessary for a product, invisible to an examiner. Keep them minimal.

**The one risk to watch.** The application is more *fun* to build than the benchmark harness, and it gives faster visible progress. That is precisely why it is dangerous. Your thesis is the numbers. Build the boring thing first.
