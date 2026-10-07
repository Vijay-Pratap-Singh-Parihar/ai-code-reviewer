# revu vs. the 2026 AI code review landscape

*Research snapshot taken 2026-10-08. Vendor pages change monthly, and so do prices and leaderboard positions. Every factual claim cites a source from the list at the end as `[S#]`. Claims marked **(third-party)** come from secondary sites and were not confirmed on a vendor page. Claims marked **(unverified)** could not be confirmed at all. Statements about revu come from this repository: `README.md`, `Product_Architecture_FullStack.md`, `IMPLEMENTATION_PLAN.md` and the code under `packages/`.*

---

## 1. Executive summary

- **The market is crowded and the leaders are converging.** CodeRabbit, Greptile, Qodo, GitHub Copilot, Cursor Bugbot, Anthropic's Claude Code Review and OpenAI's Codex now all advertise whole-repository context. Most also advertise a verification or "judge" stage that filters out false positives [S12][S13][S22][S25]. Repository context and a verifier were differentiators in 2024. In 2026 they are expected of every tool.
- **No tool is close to solving the problem.** On Martian's independent Code Review Bench, the best F1 scores sit around 60–65%, and "no tool exceeds 63%" in detecting known issues [S2][S3][S4]. A 2026 academic study found that developers adopt only 16.6% of AI-agent review suggestions, against 56.5% of human ones [S8].
- **Benchmarks are contested.** Greptile, Qodo, CodeRabbit and cubic each claim the #1 spot on the *same* Martian benchmark, at different dates and with different configurations [S3][S4][S42]. Greptile's own CEO calls performance claims "ephemeral and subjective" [S19]. Use only benchmarks with open data and an open judge (Section 5).
- **revu's credible angles are narrow but real:**
  1. Anyone can self-host the entire stack, with any LiteLLM-supported model. Competitors keep both behind an enterprise contract.
  2. Branch memory is a *persistent, incremental, inspectable* per-branch code graph.
  3. The citation verifier is *deterministic*, not LLM-judged, and it reports its drop rate.
  4. Every review records its own tokens and cost. One recorded live `cross_file` review cost $0.031, against a documented $15–25 average for Claude Code Review [S25].
- **revu is clearly behind on maturity:**
  - It indexes Python only.
  - It does not yet post comments back to GitHub.
  - It does not learn from developer feedback.
  - It supports GitHub only.
  - It has no SOC 2.
  - Its `cross_file` review is a single agent, where the leaders run multi-agent fleets.
  - It has **no benchmark numbers**.
- **Recommendation:** do not claim "better than CodeRabbit/Greptile" overall. Claim specific, measurable properties instead: cost per PR, false-positive rate on clean PRs with and without the verifier, the gain from `cross_file` on repository-level issues, and cold versus warm index cost. Measure them on Martian's offline set, the AACR-Bench Python subset and SWR-Bench clean PRs, using the published judges (Section 5).

---

## 2. What revu actually is (verified against the repo)

| Trait | Status in code/docs |
|---|---|
| GitHub App with webhook auto-review | **Built** (Stage 10). Auto-review is opt-in per repo and **off by default**. Draft PRs are skipped and a head SHA is never reviewed twice. Auto-review always uses `diff_only`. Webhooks are verified with HMAC-SHA256 and de-duplicated by delivery ID (`README.md`). A live round trip against real github.com is still pending (`IMPLEMENTATION_PLAN.md` stage map). |
| `diff_only` tier | **Built**: one LiteLLM call over the PR title, body and diff, with JSON-schema output (Stage 3). |
| `cross_file` tier | **Built**: a LangGraph tool loop with the tools `read_file`, `graph_query`, `find_definition` and `find_callers`, seeded with a token-budgeted, k-hop context bundle and capped at a maximum number of tool rounds (`agents/cross_file.py`). It runs only on request, never automatically. |
| Code graph | **Built, Python only**: tree-sitter plus `rustworkx`, with **name-based** call resolution and no type inference. The plan reports its accuracy as honestly low for this repo's own code (README; Stage 4). |
| Branch memory | **Built**: a `BranchIndex` row per build attempt, incremental reparse of changed files, staleness states, force-push / non-fast-forward detection that triggers a full rebuild, and merge-base resolution (`index/vcs.py`). |
| Verifier (Stage 8) | **Built**: dedup, then a drop for any finding whose cited file or line does not exist, then confidence recomputed from agent agreement and surviving evidence, then threshold and cap. It applies to `cross_file` only. It checks that a citation *exists*, not that the claim is *true*. |
| Cost accounting | **Partly built**: `tokens_in`, `tokens_out` and `cost_usd` are stored on each `analysis_run`. A `token_usage_ledger` table exists, but the worker does not write to it. Quotas, the pre-flight gate and the result cache are Stage 12, a stretch goal. |
| Tenant isolation | **Built**: Postgres row-level security on every tenant table, plus composite foreign keys, a non-superuser app role, and a startup refusal in production if that role could bypass RLS. Covered by tests. |
| Signed index blobs | **Built**: `MAGIC + HMAC-SHA256 + gzip` with constant-time verification (`index/store.py`). |
| LLM providers | **Built**: everything goes through LiteLLM, and the model is chosen by environment variable. **Planned**: per-org DB-backed providers and BYO OpenAI-compatible endpoints (Stage 11). Pointing LiteLLM at a local endpoint through environment variables is plausible but **untested** in this repo. |
| Posting findings to GitHub | **Not built.** Findings appear only in revu's own UI, the diff view with its evidence trail (Stage 11 backlog). |
| Learning from feedback / Outdated Rate | **Not built**: the `developer_action` column exists but is not populated. |

---

## 3. Comparison table

Legend: "Ent." means enterprise contract only. "?" means not publicly documented.

| Tool | Deployment | BYO / self-hosted LLM | How it gathers context | False-positive / hallucination controls | Privacy posture | Pricing (Oct 2026) | Published numbers |
|---|---|---|---|---|---|---|---|
| **revu** | Self-hosted by default (Docker Compose). Multi-tenant SaaS shape with Postgres RLS. GitHub only. | Any LiteLLM model via env today. Per-org BYO / OpenAI-compatible endpoint is **planned**. | `diff_only`: the diff. `cross_file`: a persistent per-branch tree-sitter graph (**Python only**), k-hop context bundle, and a tool-using LangGraph agent. | Deterministic citation-existence check, dedup, confidence recalibration, threshold and cap. **No** feedback learning. | Code stays on the host. LLM retention depends on the chosen provider. No SOC 2. | Free. You pay LLM tokens, recorded per run. | **None.** One live case: `cross_file` found a real cross-file bug at confidence 0.98 for $0.031 (README). |
| **CodeRabbit** | SaaS. EU SaaS option. Self-hosted container on Ent. [S9][S11] | Self-hosted customers can run NVIDIA Nemotron for context summarisation. Frontier OpenAI/Anthropic models do the reasoning [S11]. General BYO-model support: ? | Clones the repo into an ephemeral sandbox, builds a per-PR code graph, and uses a vector DB of past "learnings" [S12] (from a search summary of the blog). | Verification agents, plus learnings from feedback [S12]. | SOC 2 Type II and GDPR. No code retained after review except optional encrypted cache. Never used for training [S10]. | $30 / $60 / $90 per developer per month (monthly), with per-hour review rate limits and $0.25 per file overage [S9]. | Each vendor and date gives a different Martian position (see Section 5). In Martian v0 it had the highest online recall, 0.54 [S2]. |
| **GitHub Copilot code review** | GitHub-hosted, running on Actions runners (self-hosted runners supported) [S13] | **No model choice**: "Model switching is not supported" [S15]. | Agentic tool calling over the repository (code, directory structure, references), plus MCP and agent skills [S13][S15]. | Tuned for "less noise". Does not count as an approval by default. Docs warn it "will make mistakes" [S15]. | Not stated in the review docs [S15]. | From 2026-06-01: AI Credits plus Actions minutes on private repos [S14]. A 13× premium-request multiplier is reported **(third-party)**. | Martian: ~62.6% F1 at one snapshot **(third-party, unverified)**. |
| **Greptile** | SaaS. Self-hosted via Docker Compose or Helm, air-gapped on Ent. [S16][S18] | Yes, when self-hosted: any OpenAI-compatible base URL and key [S17]. | Repository-wide graph of functions, classes and dependencies [S18]. Parallel agents **(third-party)**. | Learns from 👍/👎 reactions, "stops commenting on things you don't care about" after 2–3 weeks [S18]. | SOC 2 Type II. Chat logging can be turned off. Self-hosted logs stay on customer servers [S17]. | Free (50 credits). Pro $30/seat with 50 credits, $1 per extra credit. Base/Plus/Apex reviews cost 1/3/10 credits [S16]. | Own benchmark (Jul 2025): 82% catch rate vs Bugbot 58%, Copilot 54%, CodeRabbit 44%, Graphite 6% [S5]. Martian (Jul 2026 snapshot): F1 60.8%, precision 76.2%, recall 50.6% [S3]. |
| **Qodo** (formerly Qodo Merge) | SaaS. Single-tenant, on-prem or air-gapped on Ent. [S21] | BYOK on Ent. [S21] | "Context engine" over the full repo. Parallel specialist agents plus a **judge agent** that dedups and drops low-confidence findings [S22]. | Judge agent, rules system, "self-learning" on Ent. [S21][S22] | "Does not train models on your code". MSA/DPA available [S21]. | Pro Team $30/month base with pooled credits at $0.012 each (~18–144 reviews/month depending on pack) [S21]. | Own benchmark: F1 60.1%, recall 56.7% [S22]. Martian: "Qodo Extended" 64.3% F1, a research-preview configuration [S4]. |
| **PR-Agent** (open source) | Self-hosted: GitHub Action, CLI, Docker, webhooks. Supports GitHub, GitLab, Bitbucket, Azure DevOps and Gitea [S20]. | **Yes**: anything LiteLLM reaches, including Ollama [S20]. | The diff, with a "PR compression" strategy [S20]. No repo graph. | Prompt-level only. ? | Fully under your control. | Free (MIT). Pay LLM cost [S20]. | ? Being donated to an open-source foundation [S20]. |
| **Cursor Bugbot** | SaaS (Cursor) | ? | ? | **Learned rules** from reactions, replies and human comments, kept or disabled by signal [S23]. | ? | Reported $40/user/month and a move to usage-based billing in 2026 **(third-party)**. | Own measurement: 78.13% "resolution rate" over 50,310 public PRs, judged by an LLM (Greptile 63.49%, CodeRabbit 48.96%) [S23]. |
| **Graphite Agent** (formerly Diamond) | SaaS. Cursor agreed to acquire Graphite in Dec 2025 [S31]. | ? | "Full codebase context" **(third-party)** | ? | ? | ? | 6% on Greptile's benchmark [S5]. Highest precision but lowest recall in Martian v0 [S2]. |
| **Claude Code Review** (Anthropic) | Managed (Anthropic infra), Team/Enterprise research preview. Self-run alternative: GitHub Actions / GitLab CI [S25]. | Managed: no. Unavailable to Zero-Data-Retention orgs [S25]. | A fleet of specialised agents over the full codebase [S25]. | A **verification step checks candidates against actual code behaviour**, then dedup and severity ranking. `REVIEW.md` can require `file:line` evidence [S25]. | Not offered under ZDR, HIPAA or the BAA [S25]. | Token-billed, **$15–25 per review on average**. Averages ~20 minutes [S25]. | Vendor: <1% of findings marked incorrect **(third-party report of Anthropic data)**. |
| **OpenAI Codex review** | ChatGPT/Codex cloud. `@codex review` or automatic [S26]. | No | The diff plus `AGENTS.md` "Code Review Rules" [S26]. Martian lists a "ChatGPT Codex Connector" [S3]. | Posts **P0/P1 issues only** [S26]. | ? | Included in ChatGPT plans **(third-party)**. | Martian: F1 59.4% (Jul 2026 snapshot) [S3]. |
| **Gemini Code Assist (GitHub)** | GitHub app. A separate enterprise version runs under Google Cloud terms [S27]. | No | "Automatically retrieve helpful information from the repository" [S27]. Style guides per repo or org-wide [S27]. | Skips `.github/workflows` [S27]. | Not stated on the review page [S27]. | Reported $19 Standard / $45 Enterprise per user per month (annual) **(third-party)**. | ? |
| **Amazon CodeGuru Reviewer / Q Developer** | CodeGuru Reviewer is in **maintenance mode**: no new repository associations since 2025-11-07 [S29]. Q Developer reviews GitHub PRs [S30]. | No | SAST, secrets, SCA and quality detectors [S30]. | ? | AWS | ? | ? |
| **Sourcery** | SaaS. Self-hosting on Ent. [S32] | BYO LLM keys on Ent. [S32] | ? | ? | SOC 2 Type II. "Never used to train any model" [S32]. | Free for OSS. $12 / $24 per developer per month (annual) [S32]. | ? |
| **Bito** | SaaS or self-hosted Docker agent [S33] | ? | A codebase knowledge graph shared with "AI Architect" [S33]. | ? | ? | ? | Marketing claims only [S33]. |
| **Macroscope** | SaaS | ? | AST-based graph of how the codebase works, plus issue-tracker context [S34]. | Claims "4× less noise" [S34]. | SOC 2 Type II [S34]. | Usage-based, ~$0.95 per review [S34]. | Own benchmark claims; methodology not on the page [S34]. |
| **Baz** | SaaS (also on AWS Marketplace) [S35] | ? | Bugs and "architectural regressions"; Planner product [S35]. | ? | ? | ? | Martian lists Baz among the tools it tested [S40]. |
| **cubic** | SaaS | ? | ? | ? | ? | ? | Claims #1 on Martian at its own snapshot [S42] (unverified numbers). |
| **Ellipsis / Korbit / CodeAnt** | SaaS | ? | ? | ? | ? | Ellipsis: tokens at cost + 10% + compute; Korbit reportedly acquired by Boost Security in May 2026 **(third-party, [S36])**. CodeAnt: ? | ? |
| **Sweep** | Pivoted to a JetBrains coding assistant. No longer a PR reviewer **(third-party, [S37])**. | n/a | n/a | n/a | n/a | n/a | n/a |
| **Browser extensions** (Qodo extension, ThinkReview, Probe) | Run in the client's browser [S38][S39] | ThinkReview and Probe are BYO-key / local-model oriented **(third-party)** [S39] | Usually only what the PR page shows: the diff and files. | None beyond the prompt. | Strongest privacy, weakest context. | Mostly free | None |

---

## 4. Where revu can credibly differentiate

Each claim below is phrased so that a measurement can prove or disprove it. None of them has been measured yet.

### 4.1 Full self-hosting with free model choice, without an enterprise contract
- **Evidence:**
  - revu runs entirely from `docker compose up`, and every model call goes through LiteLLM.
  - Among competitors, self-hosting and BYO models are **enterprise-gated** at CodeRabbit [S9][S11], Greptile [S16][S17], Qodo [S21] and Sourcery [S32].
  - Copilot forbids model choice outright [S15].
  - Claude Code Review is unavailable to Zero-Data-Retention orgs [S25].
- **Honest caveat:** PR-Agent is already free, self-hosted and LiteLLM-based [S20], so self-hosting alone is not unique. revu's distinct combination is self-hosting *plus* a repository graph *plus* an agent, all open to inspection. Per-org BYO endpoints are still **planned**, and a local-model run has not been tested.
- **What would prove it:** a reproducible run of the same benchmark with a frontier API model and with a local open-weights model served through an OpenAI-compatible endpoint (vLLM or Ollama), reporting F1 and cost for each. That gives a quality-versus-privacy curve, which no closed vendor publishes.

### 4.2 Persistent, incremental branch memory with explicit cost accounting
- **Evidence:**
  - revu keeps one versioned graph per branch.
  - It reparses only changed files.
  - It detects force-pushes and rebuilds fully.
  - It records the update mode and duration of each build.
- **Competitor comparison:**
  - CodeRabbit rebuilds a code graph per PR in an ephemeral sandbox [S12].
  - Greptile also maintains a repository graph [S18], so persistence is not unique.
  - No vendor publishes index cost, freshness or rebuild frequency.
- **What would prove it:**
  - Cold build time versus warm incremental update time per merged commit, on 3–5 real Python repos with replayed history.
  - Rebuild frequency under force-pushes.
  - The marginal context-retrieval cost per PR with a warm index versus rebuilding per PR.
  - This is a systems result, and no competitor reports it.

### 4.3 A deterministic, auditable hallucination check
- **Evidence:** revu's verifier drops findings whose cited file or line does not exist and reports the drop rate. It needs no LLM, so it is cheap, reproducible and cannot itself hallucinate.
- **Competitor comparison:** competitors use *LLM-based* verification: Qodo's judge agent [S22], Claude's verification step [S25] and CodeRabbit's verification agents [S12].
- **Honest caveat:** existence of a citation is a weak check. It catches fabricated locations, not wrong reasoning about real code. Claude's verifier "checks candidates against actual code behaviour" [S25], which is stronger.
- **What would prove it:** an ablation with the verifier on and off on SWR-Bench, which contains 500 *clean* PRs where any finding is a false positive [S7], plus the Martian offline set. Report:
  - precision
  - false positives per clean PR
  - recall lost (true findings wrongly dropped)
  - the share of raw findings with non-existent citations

  A cheap deterministic filter that recovers most of an LLM judge's precision gain at near-zero cost would be a publishable result.

### 4.4 Cost per review that is transparent and orders of magnitude lower
- **Evidence:** every revu run stores its tokens and USD cost. The one live `cross_file` review cost $0.031.
- **Competitor costs:**
  - Claude Code Review averages $15–25 per review [S25].
  - Copilot now consumes credits plus Actions minutes [S14].
  - Greptile's Apex reviews cost 10 credits [S16].
  - Macroscope averages ~$0.95 per review [S34].
- **Honest caveat:** one sample is an anecdote. Cheapness is only a virtue at comparable quality. revu also has no cache and no quota enforcement yet.
- **What would prove it:** F1 per dollar and the cost distribution (median and p95) over the full benchmark run, next to the published F1 of competitors at their published per-review prices.

### 4.5 Explainable findings and a safe default
- **Evidence:**
  - The UI shows, for each finding, which files were retrieved and why (`retrieval_reason`).
  - Auto-review is off by default and never uses the expensive tier.
- **Competitor comparison:** Claude's "Why this was flagged" section [S25] and CodeRabbit's "explainable reviews" posts [S12] show that competitors are moving the same way. This is a design strength, not a unique one.
- **What would prove it:** a small user study (n≈5–10 developers) rating trust and usefulness with and without the evidence trail. Alternatively, run Outdated Rate (adoption) once comment posting exists. That is the metric the adoption literature calls unresolved [S8].

### 4.6 Tenant isolation enforced in the database
- **Evidence:** Postgres row-level security, composite foreign keys, a non-bypass role and catalog tests.
- **Honest caveat:** this is sound engineering but invisible to buyers. Competitors answer the same question with SOC 2 Type II reports [S10][S17][S32][S34]. Present it as "security by construction, testable", not as a market differentiator.

---

## 5. Where revu is behind (candidly)

1. **No benchmark numbers at all.** Every serious competitor has a published F1 or catch rate (Section 6). The roadmap explicitly deferred the evaluation harness. That is the single largest gap for an evaluator.
2. **Python-only indexing, with name-based call resolution.** Martian's offline set spans Python, Go, TypeScript, Ruby and Java. AACR-Bench spans 10 languages [S6]. `cross_file` cannot run on most of either.
3. **Findings never reach the PR.** No inline comments, no check run, no suggested fixes. Every competitor posts inline. Claude also posts check-run annotations [S25], and Bugbot, Graphite and Macroscope offer autofix [S23][S34].
4. **No learning loop.** Bugbot's learned rules [S23], Greptile's reaction learning [S18] and CodeRabbit's learnings DB [S12] all adapt to each team. revu's `developer_action` column is empty.
5. **One agent, not a fleet.** Claude [S25] and Qodo [S22] run parallel specialist agents with a judge. revu's broader agent fan-out is deferred.
6. **The verifier is shallow.** It checks citation existence, not semantic validity, and it is skipped for `diff_only`, which is the auto-review path.
7. **GitHub only.** PR-Agent supports five forges [S20], and Qodo also supports Gerrit [S21].
8. **No compliance story.** No SOC 2, DPA or retention policy. With a third-party LLM, code goes to that provider under its own terms.
9. **Not proven at scale.** The live GitHub round trip is pending. There are no production users, no rate limiting or quota enforcement, and no result cache.
10. **The market has moved.** Repository context, agentic tools and verification are now table stakes [S13][S22][S25]. Claiming that "existing tools are diff-only" would be **false in 2026**, so do not make that claim to an evaluator. It is true only of PR-Agent, browser extensions and older tools.

---

## 6. Existing benchmarks and how to run a fair head-to-head

### 6.1 Benchmarks available

| Benchmark | What it is | Strengths | Weaknesses | Usable by revu? |
|---|---|---|---|---|
| **Martian Code Review Bench** [S1][S2] | Offline: 50 PRs from Sentry, Grafana, Cal.com, Discourse and Keycloak, with 173 human-verified "golden comments". An LLM judge does semantic matching, cross-checked with 3 judge models. Online: precision measured as the share of bot comments developers acted on, over 200k+ real PRs [S1][S40]. Dataset, judge prompts and pipeline are open, and it accepts any tool [S2]. | Independent, open, and the de-facto industry leaderboard. It has published numbers for ~10+ commercial tools. | Recall is capped by gold-set completeness. The PRs are old, so contamination is possible. The judge is not human-calibrated [S1]. Positions shift monthly, and vendors cherry-pick snapshots [S3][S4]. | **Yes, best option.** Run `diff_only` on all 50 PRs. Run `cross_file` on the Python PRs (Sentry's backend). The online track needs real deployments, so not yet. |
| **Greptile bug benchmark** [S5] | 50 real bugs from 5 OSS repos, reintroduced as fresh PRs, measured as catch rate per tool (Jul 2025). | Simple, and it covers severity tiers. | Run by a vendor. Recall-only, so noise is not penalised. Now dated. | As a secondary recall check only. |
| **AACR-Bench** (Alibaba et al., 2026) [S6] | 200 PRs and 1,505 expert-verified comments from 50 repos in 10 languages. Each comment is labelled by the context it needs: diff (754), file (518) or repository (233). | It is the **only** benchmark that labels which issues need repository context, which is exactly what tests `cross_file`. Expert-verified, academic. | Low absolute scores (e.g. Claude-4.5-Sonnet agent: precision 39.9%, recall 10.1%). Strong per-language variance [S6]. | **Yes**, on the Python subset. Report F1 split by diff, file and repo context levels. Already named in revu's original roadmap (Phase 0). |
| **SWR-Bench** [S7] | 1,000 manually verified PRs with full project context: 500 change-PRs and 500 **clean** PRs. | Measures false positives directly on clean PRs. | Language coverage not checked **(unverified)**. | **Yes**, for the verifier ablation. |
| **Vendor-internal metrics** | Bugbot "resolution rate" [S23], Qodo's own F1 [S22], Macroscope's detection rate [S34] | Large scale | Non-reproducible, and they use an LLM judge chosen by the vendor. | Cite only as context, never as a comparison. |

### 6.2 Proposed fair protocol for the evaluator

1. **Primary:** the Martian offline set, using its published judge prompts and the *same* judge model as the leaderboard snapshot being compared against. Report precision, recall and F1 for:
   - `diff_only` (all PRs)
   - `cross_file` with the verifier (Python PRs only)
   - `cross_file` without the verifier

   Put them next to the published leaderboard rows with the snapshot date stated. Do not claim a rank. Report "falls between X and Y".
2. **Context-value test:** the AACR-Bench Python subset. The hypothesis is that `cross_file` beats `diff_only` on repo-level comments without losing precision on diff-level ones. This test isolates revu's architectural bet.
3. **Noise test:** SWR-Bench clean PRs. Report false findings per clean PR and the verifier's drop rate and recall cost.
4. **Efficiency:** cost per PR (median and p95), latency, and cold versus warm index time. Compare against the published price points [S9][S16][S25][S34].
5. **Statistics:** paired comparisons on the same PRs (Wilcoxon signed-rank, or a bootstrap 95% CI on F1). Run each configuration ≥3 times to measure LLM nondeterminism. Fix the model, temperature and prompt versions.
6. **Threats to validity, stated up front:**
   - Python-only `cross_file` gives a smaller sample.
   - The judge is an LLM.
   - The OSS PRs may be in training data.
   - Competitor numbers come from their own runs and dates, not from a simultaneous re-run.
7. **Budget note:** each step makes real, billed LLM calls. Estimate the cost (tokens × PRs × repeats) and get approval before running it.

---

## 7. Known weaknesses users report across the category

- **Noise and alert fatigue.** False positives are widely described as the top reason teams abandon these tools **(third-party summaries, e.g. pyor.review, CodeAnt blogs)**. The academic evidence is firmer:
  - AI-agent suggestions are adopted at 16.6% versus 56.5% for human ones.
  - Over half of unadopted AI suggestions were incorrect or were fixed some other way [S8].
- **Low recall even for the leaders.** Top tools miss roughly 35–50% of known issues on Martian [S2][S3]. Academic agents reach about 10% recall on AACR-Bench [S6].
- **Repository context does not help automatically.** On AACR-Bench, naive retrieval (BM25 or embeddings) *lowered* F1 for some models, for example Claude-4.5-Sonnet fell from 14.46% to 9.98% with BM25. Agentic methods improved on repository-level issues [S6]. This supports targeted, graph-guided context over bulk context, which is revu's design.
- **Cost unpredictability.** Copilot review moved to credits plus Actions minutes [S14]. Claude Code Review costs $15–25 per review and scales with pushes [S25]. Bugbot moved to usage-based billing **(third-party)**.
- **Privacy constraints.** Claude's managed review is unavailable under Zero Data Retention or HIPAA [S25]. Self-hosting and BYO models are enterprise-only at most vendors [S9][S16][S21][S32].
- **Independence concerns.** Greptile argues that review should be done by a different agent than the one that wrote the code [S19]. That is relevant now that Cursor owns Graphite [S31] and the coding-agent vendors (Anthropic, OpenAI, GitHub) all ship reviewers.

---

## 8. Sources

- [S1] Martian, Code Review Bench methodology: https://github.com/withmartian/code-review-benchmark/blob/main/methodology/full.md
- [S2] Martian, "Code Review Bench: Towards Billion Dollar Benchmarks" (v0, 2026-02-26): https://withmartian.com/post/code-review-bench-v0 · leaderboard: https://codereview.withmartian.com/
- [S3] Greptile, "Greptile Ranks #1 on Martian's AI Code Review Benchmark" (2026-07-30): https://www.greptile.com/content-library/greptile-martian-code-review-benchmark
- [S4] Qodo, "Qodo Ranked #1 … in Martian's Code Review Benchmark" (2026-03-15): https://www.qodo.ai/blog/qodo-ranked-1-ai-code-review-tool-in-martians-code-review-benchmark/
- [S5] Greptile, AI code review benchmarks (Jul 2025): https://www.greptile.com/benchmarks
- [S6] AACR-Bench (arXiv 2601.19494): https://arxiv.org/html/2601.19494 · data: https://github.com/alibaba/aacr-bench
- [S7] SWR-Bench (arXiv 2509.01494): https://arxiv.org/abs/2509.01494
- [S8] "Human-AI Synergy in Agentic Code Review" (arXiv 2603.15911): https://arxiv.org/html/2603.15911v1
- [S9] CodeRabbit pricing: https://www.coderabbit.ai/pricing
- [S10] CodeRabbit FAQ: https://www.coderabbit.ai/faq
- [S11] CodeRabbit, Nemotron support for self-hosted (2026-01-05): https://www.coderabbit.ai/blog/coderabbit-ai-code-reviews-now-support-nvidia-nemotron
- [S12] CodeRabbit, "The art and science of context engineering": https://www.coderabbit.ai/blog/the-art-and-science-of-context-engineering (content taken from a search-result summary, not fetched in full)
- [S13] GitHub Changelog, Copilot code review agentic architecture (2026-03-05): https://github.blog/changelog/2026-03-05-copilot-code-review-now-runs-on-an-agentic-architecture/
- [S14] GitHub Changelog, Copilot code review Actions minutes (2026-04-27): https://github.blog/changelog/2026-04-27-github-copilot-code-review-will-start-consuming-github-actions-minutes-on-june-1-2026/
- [S15] GitHub Docs, Copilot code review: https://docs.github.com/en/copilot/concepts/agents/code-review
- [S16] Greptile pricing: https://www.greptile.com/pricing
- [S17] Greptile security / self-host: https://www.greptile.com/security · https://www.greptile.com/docs/security/selfhost
- [S18] Greptile docs, introduction: https://www.greptile.com/docs/introduction
- [S19] Greptile, "There is an AI Code Review Bubble" (2026-01-24): https://www.greptile.com/blog/ai-code-review-bubble
- [S20] PR-Agent repository: https://github.com/qodo-ai/pr-agent
- [S21] Qodo pricing: https://www.qodo.ai/pricing/
- [S22] Qodo, "Introducing Qodo 2.0": https://www.qodo.ai/blog/introducing-qodo-2-0-agentic-code-review/
- [S23] Cursor, "Bugbot now self-improves with learned rules" (2026-04-08): https://cursor.com/blog/bugbot-learning
- [S25] Claude Code docs, Code Review: https://code.claude.com/docs/en/code-review
- [S26] OpenAI/ChatGPT docs, Codex GitHub integration: https://learn.chatgpt.com/docs/third-party/github
- [S27] Google Cloud docs, Gemini Code Assist review: https://docs.cloud.google.com/gemini/docs/code-review/review-repo-code
- [S29] AWS, CodeGuru Reviewer availability change: https://docs.aws.amazon.com/codeguru/latest/reviewer-ug/codeguru-reviewer-availability-change.html
- [S30] AWS, Amazon Q Developer GitHub code reviews: https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/github-code-reviews.html
- [S31] InfoWorld, "Cursor owner Anysphere agrees to buy Graphite": https://www.infoworld.com/article/4110558/cursor-owner-anysphere-agrees-to-buy-graphite-code-review-tool.html
- [S32] Sourcery pricing: https://www.sourcery.ai/pricing
- [S33] Bito docs, self-hosted review agent: https://docs.bito.ai/ai-code-review-agent/install-run-as-a-self-hosted-service/prerequisites
- [S34] Macroscope, AI code review: https://macroscope.com/ai-code-review
- [S35] Constellation Research, "Startups to know: Baz": https://www.constellationr.com/insights/news/startups-know-baz-brings-governance-planning-specs-ai-generated-code
- [S36] cubic, "AI code review pricing in 2026" (third-party, competitor-authored): https://www.cubic.dev/blog/ai-code-review-pricing
- [S37] AIWiki, Sweep (third-party): https://www.aiwiki.ai/wiki/sweep_ai
- [S38] Qodo docs, Chrome extension: https://docs.qodo.ai/v1/integrations/chrome-extension
- [S39] ThinkReview (AGPL browser extension): https://discuss.ai.google.dev/t/project-share-thinkreview-an-agpl-3-0-browser-extension-for-private-agentic-code-reviews-github-gitlab-bitbucket-ado/171023
- [S40] Kilo, "Martian's Independent Benchmark Tested 13 Code Review Tools" (2026-03-05): https://blog.kilo.ai/p/martians-independent-benchmark-tested
- [S42] cubic, "cubic is the #1 AI code reviewer on Code Review Bench": https://www.cubic.dev/blog/cubic-is-the-best-ai-code-reviewer-on-martian-s-benchmark · CodeRabbit, "CodeRabbit tops independent AI code review benchmark": https://www.coderabbit.ai/blog/coderabbit-tops-martian-code-review-benchmark (headline claims; numbers not verified)

*Not used as evidence (could not verify): the Copilot "13× multiplier", Gemini seat prices, Bugbot's $40 seat price, CodeRabbit's "$15K/month self-hosted minimum", Anthropic's "<1% incorrect" figure, and the various "Top AI Tracker" and aggregator rankings. Where any of these appear in the table, they are marked third-party.*
