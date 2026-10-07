# Literature Review: Grounding the Design of revu

*Prepared 2026-10-08. Every paper below was checked against its arXiv, ACL Anthology, DOI or publisher page on that date. Bracketed numbers point to the References section. Where a venue or detail could not be confirmed, the entry says so.*

## 0. Scope and how to read this document

revu is an AI pull-request reviewer built in layers:

1. a **diff-only single-call reviewer**;
2. a **cross-file LangGraph agent** that uses tools over a tree-sitter + rustworkx code graph (callers, callees, imports);
3. **k-hop change-impact retrieval**, ranked by graph proximity plus BM25 and packed into a token budget;
4. a **verifier** that drops findings whose cited evidence lines do not exist, removes duplicates, and recalibrates confidence from agent agreement and evidence survival;
5. an incremental per-branch index, a per-run cost ledger and multi-tenant row-level security (RLS);
6. a planned bring-your-own or self-hosted LLM (OpenAI-compatible), with LoRA fine-tuning compared against RAG.

Each theme below lists the key papers. Every entry ends with a **Relevance to revu** line that names the design choice the paper supports or the gap it leaves open. Section 9 lists the research gaps. Section 10 gives a benchmark plan the student can run.

**Note on `Product_Architecture_FullStack.md` §8.** That table cites several numbers. Here are the verified sources:

| Number in the table | Verified source |
|---|---|
| 73.8% resolved, longer closure time | Cihan et al. [9] |
| ~75% precision with a filter stage | BitsAI-CR [10] |
| Multi-review aggregation, +43.67% F1 | SWR-Bench [20] |
| Concise, hunk-level, manually triggered comments drive change | Sun et al. [37] |
| AI adoption 16.6% vs 56.5% for humans | Zhong et al. [38] |

Two claims have **no source I could verify**:

- "bulk repository context gives no gain and costs 20% more"
- "agentic exploration: high precision, very low recall"

The closest candidates are the AACR-Bench paper [17] and the OpenCodeReview paper [12]. Check both before citing either claim in the thesis.

---

## 1. Automated code review generation

**Tufano et al., "Towards Automating Code Review Activities" (ICSE 2021) [1] and "Using Pre-Trained Models to Boost Code Review Automation" (ICSE 2022) [2].**
- The 2021 paper framed review automation as two sequence-to-sequence tasks: predicting the revised code a reviewer would request, and implementing a natural-language comment. It succeeded in up to 16% and 31% of cases respectively.
- The 2022 follow-up showed that a pre-trained T5 model improves these results on a larger, more realistic dataset, and added comment generation as a task.

*Relevance to revu:* these papers established the "diff in, comment out" formulation that revu's diff-only reviewer reproduces. revu keeps that formulation as its baseline and treats it as a lower bound, not the end goal.

**Li et al., "Automating Code Review Activities by Large-Scale Pre-training" (CodeReviewer, ESEC/FSE 2022) [3].**
- A Microsoft model pre-trained on review data from nine languages with four review-specific objectives.
- Evaluated on three tasks: quality estimation, comment generation and code refinement.
- Its dataset became the de facto benchmark for later work, and BLEU on that dataset became the headline metric.

*Relevance to revu:* revu's per-hunk "is there an issue" behaviour corresponds to CodeReviewer's quality-estimation task. The dataset is diff-only, so it cannot test revu's cross-file claims (see Theme 2).

**Li et al., "AUGER" (ESEC/FSE 2022) [4]; Hong et al., "CommentFinder" (ESEC/FSE 2022) [5].**
- AUGER tags the review lines, then fine-tunes T5 to generate comments. It reports ROUGE-L of 22.97%.
- CommentFinder is a simple information-retrieval baseline. It recommends past comments for similar changed methods and is 32% more accurate and 49 times faster than the deep-learning approaches it compares against.

*Relevance to revu:* CommentFinder is an early sign that retrieval over past reviews is a strong, cheap signal. That supports revu's planned "conventions mined from repository history" and the RAG arm in Theme 7.

**Pornprasit et al., "D-ACT" (SANER 2023) [6]; Lu et al., "LLaMA-Reviewer" (ISSRE 2023) [7].**
- D-ACT makes code transformation diff-aware and introduces a **time-wise evaluation**: train on older reviews, test on newer ones. Random splits turn out to inflate results.
- LLaMA-Reviewer fine-tunes LLaMA with LoRA and prefix tuning, training under 1% of the parameters, and matches CodeReviewer-class models.

*Relevance to revu:* D-ACT's time-wise split is the right protocol for revu's LoRA experiment. LLaMA-Reviewer is the direct precedent for that experiment (Theme 7).

**Lu et al., "Towards Practical Defect-Focused Automated Code Review" (ICML 2025, Kuaishou) [8].**
- Argues that snippet-level code-to-text generation scored with BLEU misses the actual job.
- Proposes four components, deployed on an industrial C++ codebase:
  - **code-slicing** context extraction;
  - a **multi-role** LLM framework;
  - a **false-alarm filter**;
  - workflow-aware prompts.
- Reports a 2x gain over plain LLMs and 10x over earlier baselines, measured as key-bug inclusion and false-alarm rate.

*Relevance to revu:* this is the closest published architecture to revu. Slicing plays the role of revu's k-hop graph retrieval, and the filter plays the role of revu's verifier. It strongly supports revu's three-stage design: context, multiple agents, verifier.

**Industrial deployments.** Each of these reports a different practical lesson:

| Deployment | Source | What it reports |
|---|---|---|
| Google | Frömmgen et al. [13] | ML-suggested edits resolve 7.5% of reviewer comments |
| Google | Vijayvergiya et al., AutoCommenter [14] | LLM best-practice comments rolled out to tens of thousands of developers |
| Meta | Maddila et al., MetaMateCR [15] | A fine-tuned Llama produces exact-match fixes in 68% of offline cases, but the first UI slowed reviewers by more than 5%, so safety trials were needed |
| Beko | Cihan et al. [9] | 73.8% of comments resolved, but PR closure time rose from 5h52m to 8h20m |
| ByteDance | BitsAI-CR [10] | Two-stage RuleChecker + ReviewFilter reaches 75% precision for 12k+ weekly users; introduces "Outdated Rate" as an adoption metric |
| Atlassian | RovoDev, Tantithamthavorn et al. [11] | 38.7% of comments led to code changes; PR cycle time fell 30.8% |
| Ericsson | Ramesh et al. [16] | LLM combined with static analysis, early positive results |
| Mozilla and Ubisoft | Olewicki et al., RevMate [36] | Only 8.1% and 7.2% of comments accepted |

*Relevance to revu:* the RovoDev and BitsAI-CR results show that the step from roughly 10% to 40-75% useful comments came from **context plus a dedicated quality filter**, not from a bigger model. That is the core argument for revu's verifier. Cihan et al. and MetaMateCR show that a reviewer can make delivery slower if it adds noise. That justifies revu's comment cap and confidence threshold, and measuring time-to-merge in any field trial.

**Open-source agentic reviewers (2024-2026).**
- CodeAgent (EMNLP 2024) [18] uses role-playing agents with a QA-Checker supervisor.
- OpenCodeReview, by Li et al. (2026), is the system behind `alibaba/open-code-review` [12]. It argues for "determinism over non-determinism": fixed, deterministic context gathering instead of free agent exploration. On AACR-Bench it reports up to 2.17x higher SEM-F1 than mainstream coding agents (25.10% vs 11.57%) while using 5-15x fewer tokens.

*Relevance to revu:* OpenCodeReview is revu's most important external baseline and the strongest published support for revu's choice to **seed the agent with a precomputed graph bundle rather than letting it explore freely**. Because it comes from the AACR-Bench authors' group, revu's numbers can be compared with it directly.

---

## 2. Evaluation and benchmarks for code review

**AACR-Bench: Zhang et al., "Evaluating Automatic Code Review with Holistic Repository-Level Context" (arXiv 2601.19494, 2026) [17]; dataset at `github.com/alibaba/aacr-bench`.**
- Contents: 200 real PRs from 50 active projects in 10 languages.
- Annotation: "AI-assisted, expert-verified". This raised defect coverage by 285% over the original PR comments.
- Each comment records path, line range, side, category (Security, Defect, Maintainability, Performance) and a **context-level label** (Diff, File or Repo), plus an `is_ai_comment` flag.
  > *Verification note (2026-10-08):* the arXiv abstract does not itself mention the Diff/File/Repo labels; they come from the dataset's README. Confirm the field exists in the downloaded data before quoting per-level results.
- Metrics: precision, recall, line precision and noise rate. The license is Apache-2.0.
- Comment count: the README reports 2,145 comments, while the OpenCodeReview paper [12] reports 1,505 expert-verified comments on the same 200 PRs. Record which subset is used.
- The paper's main finding is that context granularity, retrieval method and agent architecture all change performance, and that the effect differs by model and language.

*Relevance to revu:* this is exactly the benchmark revu needs. The **context-level label lets revu report recall separately for Diff, File and Repo**, which is the headline test of whether the graph-backed agent helps on Repo-level defects.

**Other context-aware benchmarks:**
- **CodeFuse-CR-Bench** [19]: 601 instances from 70 Python projects, with the issue, PR and repository state; scored by rule-based checks plus an LLM judge. No single model wins on every dimension, and models differ in how robust they are to redundant context.
- **ContextCRBench** [21]: 67,910 entries, with tasks at the hunk, line-localisation and comment-generation levels. Textual context (issues) helps more than code context alone.
- **SWR-Bench** [20]: 1,000 manually verified PRs and an LLM-based evaluation with about 90% agreement with humans. Aggregating several reviews raises F1 by up to 43.67%.
- **c-CRAB** [22], by Zhang et al. (2026): validates agent reviews with tests built from human reviews. Existing review agents (PR-Agent, Devin, Claude Code, Codex) together solve only about 40% of tasks.

*Relevance to revu:* CodeFuse-CR-Bench is the stated fallback in revu's roadmap. SWR-Bench supports revu's multi-agent aggregation. ContextCRBench suggests adding the PR description and linked issue to revu's context bundle, which revu does not currently retrieve.

**Repository-level task benchmarks.** SWE-bench [23] made it standard to evaluate on real repositories at a fixed commit, with a reproducible environment. AACR-Bench and c-CRAB follow that idea for review. *Relevance to revu:* reproducing the base commit before applying the diff is required, and revu's roadmap already plans it (Phase 0).

**Critiques of BLEU-style evaluation.**
- Zhou et al. ("Generation-based Code Review Automation: How Far Are We?", ICPC 2023) [24] showed that Exact Match and BLEU-like metrics rank models differently from partial-progress metrics.
- Naik et al., CRScore (NAACL 2025) [25], note that review is a **one-to-many** problem. CRScore is a reference-free metric grounded in claims and smells detected by LLMs and static analysers, with Spearman 0.54 against human judgement.

*Relevance to revu:* revu should not report BLEU. It should report issue-level precision and recall against the benchmark's matching protocol, plus a reference-free quality score such as CRScore for comments that match nothing in ground truth. Those comments may be valid findings the annotators missed.

**What makes a review comment useful.**
- Bacchelli and Bird (ICSE 2013) [26] found that reviews at Microsoft are less about defects than expected; understanding the change is the main need.
- Bosu et al. (MSR 2015) [27] built a classifier of useful comments at Microsoft. Useful comments point to functional defects, validation issues or API misuse; style nitpicks and questions were rated less useful.
- Turzo and Bosu (EMSE 2023) [28] replicated this at OpenDev.

*Relevance to revu:* these give a validated rubric (useful, somewhat useful, not useful) for revu's human-evaluation arm. They also justify revu's focus on defect categories over style.

---

## 3. Repository-level context and retrieval

**Ouyang et al., "RepoGraph" (ICLR 2025) [29].**
- Builds a line-level repository graph of definitions and references and retrieves **ego-graphs** around the relevant entities.
- As a plug-in to four SWE-bench systems it gives a 32.8% average relative improvement.

*Relevance to revu:* this is the direct precedent for revu's tree-sitter + rustworkx graph and k-hop retrieval, and the RepoGraph code is a named baseline. revu differs in its graph (function-level call and import edges rather than line-level def-ref edges) and in its task: review rather than repair.

**Chen et al., "LocAgent" (ACL 2025) [30]; Liu et al., "CodexGraph" (NAACL 2025) [31].**
- LocAgent parses a codebase into a directed heterogeneous graph that agents traverse with multi-hop tools. It reaches 92.7% file-level localisation, and a fine-tuned Qwen2.5-Coder-32B matches proprietary models at about 86% lower cost.
- CodexGraph lets an agent query a code graph database and argues that similarity-only retrieval has low recall on complex tasks.

*Relevance to revu:* both support giving the agent **graph tools** (callers, callees, imports) instead of only grep and read. LocAgent's open-model result also supports revu's self-hosted model plan.

**Zhang et al., "RepoCoder" (EMNLP 2023) [32]; Li et al., "CodeRAG" (arXiv 2504.10046, 2025) [33].**
- RepoCoder iterates retrieval and generation with a sparse, similarity-based retriever and gains more than 10% over in-file baselines.
- CodeRAG combines a requirement graph with a dependency/semantic code graph and agentic reasoning. Note that later versions of this arXiv entry were retitled "GraphCodeAgent".

*Relevance to revu:* these support **hybrid lexical plus structural ranking** (revu combines BM25 with graph proximity). The open question is whether revu's empty embedding slot matters, which is an ablation (Section 10).

**Long context versus retrieval.**
- Liu et al., "Lost in the Middle" (TACL 2024) [34], show that LLMs use information at the start and end of a long context far better than information in the middle.
- Xu et al. (ICLR 2024) [35] find that a 4K-context model with retrieval can match a 16K long-context model at much lower compute.

*Relevance to revu:* these are the theoretical basis for revu's **token-budgeted greedy knapsack** instead of whole-repository dumps, and for ordering the bundle by relevance. The recall-versus-budget curve (Section 10) tests this directly.

**Sun et al., "Improving LLM-Based Go Code Review through Issue-List Generation and Context Augmentation" (arXiv 2606.01859, 2026) [40].**
- Compares neighbouring-code, language-server and similar-change context for review, and finds that combining them works best.
- Pruning cut the candidate list from 7.2 to 3.1 comments without losing effectiveness.

*Relevance to revu:* this is recent, review-specific evidence that structured (language-server or graph) context beats neighbouring code. It also supports a verifier that cuts the number of comments.

---

## 4. Hallucination, verification and grounding of findings

**Zhang et al., "LLM Hallucinations in Practical Code Generation" (ISSTA 2025) [41].**
- A taxonomy of repository-level hallucinations. One top-level category is *project-context conflicts*: inventing APIs, files or symbols that do not exist.
- Retrieval reduces these hallucinations.

*Relevance to revu:* this is the failure mode revu's verifier targets. A finding that cites lines that do not exist is a project-context hallucination, and dropping it is a cheap, deterministic check.

**Dhuliawala et al., "Chain-of-Verification" (Findings of ACL 2024) [42]; Wang et al., "Self-Consistency" (ICLR 2023) [43].**
- Chain-of-Verification has the model draft an answer, plan checks, answer them independently and revise.
- Self-consistency samples several reasoning paths and aggregates them.

*Relevance to revu:* revu's confidence recalibration from **agent agreement** applies self-consistency across agents. The evidence-existence check is a deterministic form of verification that, unlike Chain-of-Verification, costs no extra LLM calls.

**LLM-assisted static analysis and false-positive triage.**
- Li et al., IRIS (ICLR 2025) [44]: LLMs infer taint specifications for CodeQL, which then finds 55 rather than 27 real vulnerabilities and lowers the false discovery rate.
- Du et al., ICSE-SEIP 2026 [45]: in an industrial study at Tencent, LLM + static-analysis hybrids remove 94-98% of false alarms at high recall, for $0.001-$0.12 per alarm.
- Du et al., LLM4PFA [46]: filters 72-96% of false positives using path-feasibility reasoning.
- Xiong and Zhang (ISSTA 2026) [47]: SWE-agent/OpenHands-style agents reduce SAST noise, but aggressive filtering can suppress true vulnerabilities.
- Wadhwa et al., CORE (FSE 2024) [48]: a proposer/ranker LLM pair in which the ranker cuts false positives by 25.8%.

*Relevance to revu:* together these support a **separate, independently measured filter stage**. They also warn that a filter trades recall for precision, so revu must report both. This matches the roadmap's plan for a P/R curve over the threshold τ.

**Calibration.** Spiess et al. (ICSE 2025) [49] show that code LLMs are poorly calibrated out of the box and that Platt scaling improves them.

*Relevance to revu:* revu's hand-chosen weights (0.5/0.2/0.3) are a heuristic calibrator. The benchmark lets revu fit them, for example with logistic regression on the dev split, and report Expected Calibration Error and a reliability diagram.

---

## 5. Agentic tool use for software engineering

**Yao et al., "ReAct" (ICLR 2023) [50].** The interleaved reason-act-observe loop that LangGraph tool loops implement. *Relevance to revu:* the conceptual basis for the cross-file agent.

**Jimenez et al., "SWE-bench" (ICLR 2024) [23]; Yang et al., "SWE-agent" (NeurIPS 2024) [51].**
- SWE-agent shows that the **agent-computer interface** (tool design, concise outputs) drives performance as much as the model does.

*Relevance to revu:* supports giving the agent a few purpose-built graph tools rather than a raw shell.

**Zhang et al., "AutoCodeRover" (ISSTA 2024) [52]; Wang et al., "OpenHands" (ICLR 2025) [53].**
- AutoCodeRover uses program-structure-aware search APIs (classes, methods) at about $0.43 per task.
- OpenHands is the main open, general agent platform.

*Relevance to revu:* AutoCodeRover is the closest precedent for structure-aware tools. OpenHands is a candidate "free exploration" baseline.

**Xia et al., "Agentless" (arXiv 2407.01489; published at FSE 2025) [54]; Kapoor et al., "AI Agents That Matter" (arXiv 2407.01502) [55].**
- Agentless shows that a fixed localise-repair-validate pipeline at $0.70 per task beat contemporary agents on SWE-bench Lite.
- Kapoor et al. argue that agents must be judged on a **joint accuracy-cost frontier**, and that accuracy-only leaderboards reward unnecessarily expensive designs.

*Relevance to revu:* these are the methodological basis for revu's per-run cost ledger, the 8-round tool cap, and reporting **cost per PR alongside F1**. Agentless and OpenCodeReview [12] also challenge the agent design: revu has to show that the agent beats a deterministic graph-bundle single call.

---

## 6. Developer perception of AI reviews

- **Olewicki et al., RevMate at Mozilla and Ubisoft (arXiv 2411.07091) [36].** RAG + LLM-as-judge comments were accepted in only 7-8% of cases, though another 15-20% were marked valuable. Refactoring comments were accepted about four times as often as functional ones. Reviewers spent a median of 43 seconds of extra time per patch.
- **Sun et al., "Does AI Code Review Lead to Code Changes?" (arXiv 2508.18771) [37].** Across 22k+ comments from 16 tools in 178 repositories, comments that are **concise, contain code snippets, are hunk-level and are manually triggered** were the ones that led to changes.
- **Zhong et al., "Human-AI Synergy in Agentic Code Review" (arXiv 2603.15911) [38].** Across 278,790 review conversations, AI suggestions were adopted far less often than human ones (16.6% vs 56.5%). More than half of the unadopted suggestions were wrong or fixed differently.
- **Cihan et al. [9]; Aðalsteinsson et al., WirelessCar (arXiv 2505.16339) [39].** Developers like AI-led reviews, but how much depends on their familiarity with the codebase and on the severity of the change.
- **Gao et al., ISSTA 2026 (arXiv 2607.24601) [56].** In a controlled experiment, explanations increase trust without increasing blind agreement.
- **Johnson et al. (ICSE 2013) [57]; Sadowski et al. (CACM 2018) [58].** These classic static-analysis studies found that false positives and poor workflow fit drive abandonment. Google keeps the effective false-positive rate below about 10% and hides checks that developers ignore.

*Relevance to revu:* this theme justifies several features:
- **noise control** through the verifier, a confidence threshold and a comment cap;
- citing **evidence lines** in every comment, which acts as the explanation;
- a `/review` re-trigger;
- tracking `developer_action` and Outdated Rate.

The gap: none of these studies asks whether **evidence-cited, graph-grounded** comments are adopted more often. revu can measure that in its deployment.

---

## 7. Self-hosted and open models, and fine-tuning for review

**Hu et al., "LoRA" (ICLR 2022) [59]; Dettmers et al., "QLoRA" (NeurIPS 2023) [60].** Low-rank adapters, and 4-bit quantised base models with adapters, make fine-tuning 7-70B models possible on one GPU. *Relevance to revu:* these are the methods for the planned "LoRA on accepted review comments" arm.

**Fine-tuning studies on review data.**
- LLaMA-Reviewer [7].
- Haider et al. (arXiv 2411.10129) [61]: QLoRA fine-tuning of open models on consumer hardware improves comment generation by 25-83%, and few-shot prompts augmented with **call graphs** improve proprietary models further.
- Begolli et al. (arXiv 2507.19271) [62]: monolingual fine-tuning beats multilingual for C# review on industrial code.

*Relevance to revu:* fine-tuning is feasible on a student budget. Haider et al.'s call-graph result is independent evidence for revu's graph context.

**RAG versus fine-tuning.**
- Ovadia et al. (EMNLP 2024) [63]: RAG consistently beats unsupervised fine-tuning for injecting new knowledge.
- Hong and Baik (arXiv 2506.11591) [64]: conditioning on retrieved past review exemplars beats both pure generation and pure retrieval for review comments.

*Relevance to revu:* for **project-specific conventions**, the prior evidence favours RAG over past accepted comments, with LoRA mainly useful for style and format adaptation. This is a falsifiable hypothesis for revu's experiment.

**Open-weight code models and privacy.**
- Hui et al., Qwen2.5-Coder (arXiv 2409.12186) [65], provides strong open models from 0.5B to 32B, and LocAgent [30] shows a fine-tuned 32B model matching proprietary ones on localisation.
- Kumar and Chimalakonda (arXiv 2412.15676) [66] use federated fine-tuning for review to **keep proprietary code private**.
- Meta's MetaMateCR [15] fine-tuned an in-house Llama and beat GPT-4o by 9 points.

*Relevance to revu:* these support the bring-your-own or self-hosted, OpenAI-compatible endpoint, both for privacy-sensitive tenants and as a model-size ablation.

---

## 8. Multi-tenant SaaS data isolation

- **AWS, "SaaS Tenant Isolation Strategies" whitepaper (2020) [67].** Defines silo, pool and bridge isolation models. Its key principle is that authentication and authorisation are not the same as isolation: in a pooled database, every data access must be scoped by tenant context. AWS now marks the paper as "for historical reference", but it is still the standard framing.
- **PostgreSQL documentation, "Row Security Policies" [68].** Explains `ENABLE`/`FORCE ROW LEVEL SECURITY` and per-command policies. It warns that table owners and superusers bypass RLS unless it is forced, which is directly relevant to how revu's migrations and roles are configured.
- **OWASP ASVS 5.0 (May 2025) [69].** Provides verifiable access-control requirements, including authorisation at the data layer, which can serve as a checklist for revu's RLS test suite.

*Relevance to revu:* together these justify the pooled database with RLS and a session-scoped tenant id. The thesis should present this as engineering practice, not as a research contribution.

---

## 9. Research gaps revu addresses

1. **Code-review-specific structural retrieval.**
   - What exists: graph-based retrieval is proven for repair, localisation and generation (RepoGraph, LocAgent, CodexGraph, CodeRAG). For review, the published systems use code slicing [8], language-server context [40] or deterministic bundles [12].
   - What is missing: no published study measures **context recall versus token budget** for k-hop call/import graphs on review ground truth labelled by context level.
   - What revu adds: that measurement on AACR-Bench.
2. **Stratified evidence on when repository context helps.**
   - What exists: AACR-Bench reports that context effects vary by model and language.
   - What is missing: a clean, controlled comparison of diff-only, graph-bundle and graph-agent configurations, with **recall reported separately for Diff, File and Repo comments**, under matched cost.
3. **Deterministic evidence grounding as a measurable stage.**
   - What exists: industry filters [10][11] are LLM-based and proprietary.
   - What revu adds: a **non-LLM evidence-existence check** with its own measured precision gain, recall cost and dollar cost. This also directly tests the hallucination taxonomy of [41] on review findings.
4. **Agent versus deterministic pipeline under a cost frontier.**
   - What exists: Agentless [54] and OpenCodeReview [12] argue for determinism.
   - What revu adds: a test of whether *tool-augmented agents seeded with a deterministic bundle* sit on a better accuracy-cost frontier [55] than either extreme.
5. **Confidence calibration for review findings.**
   - What is missing: no review system reports ECE or reliability diagrams.
   - What revu adds: agreement- and evidence-based confidence that can be calibrated against benchmark labels [49].
6. **Open-weight, self-hostable review quality under the same harness.**
   - What is missing: few studies compare frontier APIs and local open-weight models, or LoRA and RAG [63][64], on a **repository-level** review benchmark.

---

## 10. Proposed evaluation methodology

### 10.1 Datasets

- **Primary: AACR-Bench [17].** 200 PRs, 10 languages, comments labelled Diff, File or Repo.
  - Pin the dataset commit.
  - Record whether the 2,145-comment or the 1,505-comment verified subset is used.
  - Commit a 20-PR `dev` split, stratified by language, for prompt and threshold tuning, and keep the remaining 180 PRs as `test`. This is the roadmap's split; touch `test` only for the final runs.
- **Secondary, for generalisation: CodeFuse-CR-Bench [19]** (Python, 601 instances), or a stratified 100-instance sample of SWR-Bench [20]. This guards against overfitting to AACR-Bench's annotation style.
- **Optional field data:** PRs on repositories the student controls where revu is installed, recording `developer_action` and Outdated Rate [10].

### 10.2 Matching protocol

Use AACR-Bench's own matching rule, as the roadmap requires, so the numbers can be compared with [12][17]. Report two variants:

- **Strict:** same file, line ranges overlap within ±3 lines, and an LLM judge agrees the issue is the same.
- **Lenient:** same file, judged the same issue.

Validate the LLM judge before use: two people label 150 (prediction, ground-truth) pairs, and the report gives Cohen's κ between the judge and the humans. Use a judge model from a different family than the reviewer model to avoid self-preference bias.

### 10.3 Metrics (all per configuration, aggregated over PRs)

| Metric | Definition |
|---|---|
| Precision, Recall, F1 | Issue-level, against ground truth; **recall reported separately for Diff, File and Repo** (headline) |
| Line precision / localisation | Share of matched findings whose cited lines overlap the ground-truth span |
| Noise rate / false-positive rate | Share of findings that match no ground-truth comment; manually audit 100 of these to estimate how many are *valid but unlabelled* and report a corrected precision |
| Evidence-validity rate | Share of findings whose cited file and lines exist at the head commit and contain the referenced symbol; measured *before* the verifier, so it shows how often the model hallucinates |
| Verifier drop precision | Of the findings the verifier dropped, the share that were truly false (match no ground truth) |
| Context recall@B | For Repo- and File-level ground truth, the share whose target file or function is in the retrieved bundle at token budget B. This needs no LLM, so it is cheap |
| Calibration | ECE (10 bins) and Brier score of finding confidence against match/no-match |
| Cost per PR | Input and output tokens, US dollars (from the cost ledger), number of tool rounds |
| Latency per PR | Wall-clock p50 and p95, split into index and review time |
| Comments per PR | Mean and maximum; a proxy for reviewer burden [9][15] |
| Quality of unmatched comments (optional) | CRScore [25] or a Bosu-style usefulness rating [27] on a sample |

### 10.4 Systems compared (same PRs, same model unless the model is the variable)

1. **B0 Diff-only**: single call, diff text only.
2. **B1 Diff + BM25**: the top-k BM25 chunks under the same budget, with no graph.
3. **B2 Graph bundle, single call**: k-hop + BM25 ranked bundle, no tools.
4. **B3 Free-exploration agent**: same LangGraph loop with only read/grep tools and no graph. This is the "agentic exploration" foil.
5. **revu full**: graph-seeded agent, graph tools and verifier.
6. **External baselines**: `alibaba/open-code-review` [12]; PR-Agent; a RepoGraph-based [29] retrieval swapped into B2.
7. **Commercial tool**: one commercial reviewer (for example GitHub Copilot code review or CodeRabbit) run on a **30-PR subset** that has been mirrored into private forks. Record the date, the plan and the fact that its model and configuration are opaque. Report this comparison descriptively, without significance tests, because n is small and the tool cannot be controlled.

### 10.5 Ablations and sweeps (on `test`, or on `dev` where noted)

- **Verifier**: off / evidence check only / evidence + dedup / full recalibration. Sweep the threshold τ on `dev`, plot the P/R curve, then fix τ for `test`.
- **Retrieval**: k ∈ {0, 1, 2, 3}; budget B ∈ {2k, 4k, 8k, 16k, 32k} tokens; each edge type removed in turn (calls, callers, imports); ranking by graph only / BM25 only / hybrid / hybrid + embeddings (fills the unused embedding slot).
- **Agents**: each agent removed in turn; tool-round cap ∈ {0, 2, 4, 8}; self-aggregation with n ∈ {1, 3, 5} samples [20].
- **Model**: one frontier API model, one mid-tier API model, and **one local open-weight model** (for example Qwen2.5-Coder-7B and -32B [65] served through vLLM or Ollama's OpenAI-compatible endpoint), all inside the same pipeline.
- **Adaptation** (if time permits): open model + RAG over past accepted comments [64] vs + LoRA/QLoRA [60] vs both. Train on PRs merged before a cutoff date and test on PRs after it, following D-ACT's time-wise protocol [6]. Do not use AACR-Bench repositories for training.

### 10.6 Statistics

- **Unit of analysis**: the PR. Comments within a PR are correlated.
- **Confidence intervals**: paired bootstrap with 10,000 resamples of PRs, giving 95% percentile CIs for each metric and for each *difference* between configurations [70].
- **Significance**: paired Wilcoxon signed-rank on per-PR F1 or recall for the pre-registered contrasts, namely revu vs B0, revu vs B2, revu vs B3, revu vs OpenCodeReview, and verifier on vs off. Apply Holm-Bonferroni correction across these contrasts. Report Cliff's δ as the effect size.
- **Run-to-run variance**: run each LLM configuration 3 times (temperature as deployed) and report mean ± standard deviation across runs. Bootstrap over PRs × runs.
- **Cost-effectiveness**: plot F1 against $/PR for every configuration and mark the Pareto frontier [55].

### 10.7 Threats to validity and how to mitigate them

- **Data contamination.** Benchmark PRs may predate the model's training cutoff. Report results on PRs merged after each model's cutoff where possible, and include the secondary benchmark.
- **Incomplete ground truth.** Address it with the manual audit of unmatched findings described in 10.3.
- **Bias from the LLM judge.** Address it with the κ validation and the cross-family judge in 10.2.
- **Commercial tool drift.** Record the version and date of each run.
- **Budget.** Estimate cost from the dev runs before scaling up. Every paid run needs the user's explicit approval, as the project's working rules require. Context-recall sweeps cost nothing and should run first.

---

## References

[1] R. Tufano, L. Pascarella, M. Tufano, D. Poshyvanyk, G. Bavota. "Towards Automating Code Review Activities." ICSE 2021. https://arxiv.org/abs/2101.02518

[2] R. Tufano, S. Masiero, A. Mastropaolo, L. Pascarella, D. Poshyvanyk, G. Bavota. "Using Pre-Trained Models to Boost Code Review Automation." ICSE 2022. https://arxiv.org/abs/2201.06850

[3] Z. Li, S. Lu, D. Guo, N. Duan, S. Jannu, G. Jenks, D. Majumder, J. Green, A. Svyatkovskiy, S. Fu, N. Sundaresan. "Automating Code Review Activities by Large-Scale Pre-training." ESEC/FSE 2022. https://arxiv.org/abs/2203.09095

[4] L. Li, L. Yang, H. Jiang, J. Yan, T. Luo, Z. Hua, G. Liang, C. Zuo. "AUGER: Automatically Generating Review Comments with Pre-training Models." ESEC/FSE 2022. https://arxiv.org/abs/2208.08014

[5] Y. Hong, C. Tantithamthavorn, P. Thongtanunam, A. Aleti. "CommentFinder: A Simpler, Faster, More Accurate Code Review Comments Recommendation." ESEC/FSE 2022. https://doi.org/10.1145/3540250.3549119

[6] C. Pornprasit, C. Tantithamthavorn, P. Thongtanunam, C. Chen. "D-ACT: Towards Diff-Aware Code Transformation for Code Review Under a Time-Wise Evaluation." SANER 2023. https://doi.org/10.1109/SANER56733.2023.00036

[7] J. Lu, L. Yu, X. Li, L. Yang, C. Zuo. "LLaMA-Reviewer: Advancing Code Review Automation with Large Language Models through Parameter-Efficient Fine-Tuning." ISSRE 2023. https://arxiv.org/abs/2308.11148

[8] J. Lu, L. Jiang, X. Li, J. Fang, F. Zhang, L. Yang, C. Zuo. "Towards Practical Defect-Focused Automated Code Review." ICML 2025. https://arxiv.org/abs/2505.17928

[9] U. Cihan, V. Haratian, A. İçöz, M. K. Gül, Ö. Devran, E. F. Bayendur, B. M. Uçar, E. Tüzün. "Automated Code Review In Practice." ICSE-SEIP 2025. https://arxiv.org/abs/2412.18531

[10] T. Sun, J. Xu, Y. Li, Z. Yan, G. Zhang, L. Xie, L. Geng, Z. Wang, Y. Chen, Q. Lin, W. Duan, K. Sui. "BitsAI-CR: Automated Code Review via LLM in Practice." FSE 2025 Industry (per the conference listing). https://arxiv.org/abs/2501.15134

[11] K. Tantithamthavorn, Y. Zou, A. Wong, M. Gupta, Z. Wang, M. Buller, R. Jiang, M. Watson, M. Jeong, K. Chen, M. Wu. "RovoDev Code Reviewer: A Large-Scale Online Evaluation of LLM-based Code Review Automation at Atlassian." ICSE-SEIP 2026. https://arxiv.org/abs/2601.01129

[12] Z. Li, L. Zhang, X. Wu, Z. Zhuang, Y. Xu, B. Wang, S. Zhu, C. Wang, P. Zhao, X. Zheng, G. Rong. "OpenCodeReview: Determinism over Non-Determinism for Cost-Effective Agent-Based Code Review." arXiv 2608.09290, 2026. https://arxiv.org/abs/2608.09290 (code: https://github.com/alibaba/open-code-review)

[13] A. Frömmgen, J. Austin, P. Choy, E. Khrapko, M. Revaj, S. Chandra, et al. "Resolving Code Review Comments with Machine Learning." ICSE-SEIP 2024. https://research.google/pubs/resolving-code-review-comments-with-machine-learning/

[14] M. Vijayvergiya, M. Salawa, I. Budiselić, D. Zheng, P. Lamblin, M. Ivanković, et al. "AI-Assisted Assessment of Coding Practices in Modern Code Review." AIware 2024. https://arxiv.org/abs/2405.13565

[15] C. Maddila, N. Ghorbani, J. Saindon, P. Thakkar, V. Murali, R. Abreu, J. Shen, B. Zhou, N. Nagappan, P. C. Rigby. "AI-Assisted Fixes to Code Review Comments at Scale." arXiv 2507.13499, 2025 (listed at FSE 2026 Industry). https://arxiv.org/abs/2507.13499

[16] S. Ramesh, J. Bose, H. Singh, A. K. Raghavan, S. Roychowdhury, G. Sridhara, N. Saini, R. Britto. "Automated Code Review Using Large Language Models at Ericsson: An Experience Report." ICSME 2025. https://arxiv.org/abs/2507.19115

[17] L. Zhang, Y. Yu, M. Yu, X. Guo, Z. Zhuang, G. Rong, D. Shao, H. Shen, H. Kuang, Z. Li, B. Wang, G. Zhang, B. Xiang, X. Xu. "AACR-Bench: Evaluating Automatic Code Review with Holistic Repository-Level Context." arXiv 2601.19494, 2026. https://arxiv.org/abs/2601.19494 (data: https://github.com/alibaba/aacr-bench)

[18] X. Tang, K. Kim, Y. Song, C. Lothritz, B. Li, S. Ezzini, H. Tian, J. Klein, T. F. Bissyandé. "CodeAgent: Autonomous Communicative Agents for Code Review." EMNLP 2024. https://aclanthology.org/2024.emnlp-main.632/

[19] H. Guo, X. Zheng, Z. Liao, H. Yu, P. Di, Z. Zhang, H.-N. Dai. "CodeFuse-CR-Bench: A Comprehensiveness-aware Benchmark for End-to-End Code Review Evaluation in Python Projects." arXiv 2509.14856, 2025. https://arxiv.org/abs/2509.14856

[20] Z. Zeng, R. Shi, K. Han, Y. Li, K. Sun, Y. Wang, Z. Yu, R. Xie, W. Ye, S. Zhang. "SWR-Bench: Assessing LLM Performance in Real-World Code Review Comment Generation" (v1 title: "Benchmarking and Studying the LLM-based Code Review"). arXiv 2509.01494, 2025. https://arxiv.org/abs/2509.01494

[21] R. Hu, X. Wang, X.-C. Wen, Z. Zhang, B. Jiang, P. Gao, C. Peng, C. Gao. "Benchmarking LLMs for Fine-Grained Code Review with Enriched Context in Practice" (ContextCRBench). arXiv 2511.07017, 2025. https://arxiv.org/abs/2511.07017

[22] Y. Zhang, Z. Pan, I. N. B. Yusuf, H. Ruan, R. Shariffdeen, A. Roychoudhury. "Code Review Agent Benchmark" (c-CRAB). arXiv 2603.23448, 2026. https://arxiv.org/abs/2603.23448

[23] C. E. Jimenez, J. Yang, A. Wettig, S. Yao, K. Pei, O. Press, K. Narasimhan. "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?" ICLR 2024. https://arxiv.org/abs/2310.06770

[24] X. Zhou, K. Kim, B. Xu, D. Han, J. He, D. Lo. "Generation-based Code Review Automation: How Far Are We?" ICPC 2023. https://arxiv.org/abs/2303.07221

[25] A. Naik, M. Alenius, D. Fried, C. Rosé. "CRScore: Grounding Automated Evaluation of Code Review Comments in Code Claims and Smells." NAACL 2025. https://aclanthology.org/2025.naacl-long.457

[26] A. Bacchelli, C. Bird. "Expectations, Outcomes, and Challenges of Modern Code Review." ICSE 2013. https://www.microsoft.com/en-us/research/?p=164195

[27] A. Bosu, M. Greiler, C. Bird. "Characteristics of Useful Code Reviews: An Empirical Study at Microsoft." MSR 2015. https://doi.org/10.1109/MSR.2015.21

[28] A. K. Turzo, A. Bosu. "What Makes a Code Review Useful to OpenDev Developers? An Empirical Investigation." Empirical Software Engineering, 2023. https://doi.org/10.1007/s10664-023-10411-x

[29] S. Ouyang, W. Yu, K. Ma, Z. Xiao, Z. Zhang, M. Jia, J. Han, H. Zhang, D. Yu. "RepoGraph: Enhancing AI Software Engineering with Repository-level Code Graph." ICLR 2025. https://arxiv.org/abs/2410.14684 (code: https://github.com/ozyyshr/RepoGraph).

[30] Z. Chen, R. Tang, G. Deng, F. Wu, J. Wu, Z. Jiang, V. Prasanna, A. Cohan, X. Wang. "LocAgent: Graph-Guided LLM Agents for Code Localization." ACL 2025. https://aclanthology.org/2025.acl-long.426

[31] X. Liu, B. Lan, Z. Hu, Y. Liu, Z. Zhang, F. Wang, M. Q. Shieh, W. Zhou. "CodexGraph: Bridging Large Language Models and Code Repositories via Code Graph Databases." NAACL 2025. https://aclanthology.org/2025.naacl-long.7/

[32] F. Zhang, B. Chen, Y. Zhang, J. Keung, J. Liu, D. Zan, Y. Mao, J.-G. Lou, W. Chen. "RepoCoder: Repository-Level Code Completion Through Iterative Retrieval and Generation." EMNLP 2023. https://arxiv.org/abs/2303.12570

[33] J. Li, X. Shi, K. Zhang, G. Li, Z. Jin, et al. "CodeRAG: Supportive Code Retrieval on Bigraph for Real-World Code Generation" (later versions retitled "GraphCodeAgent: Dual Graph-Guided LLM Agent for Retrieval-Augmented Repo-Level Code Generation"). arXiv 2504.10046, 2025. https://arxiv.org/abs/2504.10046

[34] N. F. Liu, K. Lin, J. Hewitt, A. Paranjape, M. Bevilacqua, F. Petroni, P. Liang. "Lost in the Middle: How Language Models Use Long Contexts." TACL 12, 2024. https://arxiv.org/abs/2307.03172

[35] P. Xu, W. Ping, X. Wu, L. McAfee, C. Zhu, Z. Liu, S. Subramanian, E. Bakhturina, M. Shoeybi, B. Catanzaro. "Retrieval meets Long Context Large Language Models." ICLR 2024. https://arxiv.org/abs/2310.03025

[36] D. Olewicki, L. Da Silva, S. Mujahid, A. Amini, B. Mah, M. Castelluccio, S. Habchi, F. Khomh, B. Adams. "Impact of LLM-based Review Comment Generation in Practice: A Mixed Open-/Closed-source User Study." arXiv 2411.07091, 2024. https://arxiv.org/abs/2411.07091

[37] K. Sun, H. Kuang, S. Baltes, X. Zhou, H. Zhang, X. Ma, G. Rong, D. Shao, C. Treude. "Does AI Code Review Lead to Code Changes? A Case Study of GitHub Actions." arXiv 2508.18771, 2025 (rev. 2026). https://arxiv.org/abs/2508.18771

[38] S. Zhong, S. Noei, Y. Zou, B. Adams. "Human-AI Synergy in Agentic Code Review." arXiv 2603.15911, 2026. https://arxiv.org/abs/2603.15911

[39] F. S. Aðalsteinsson, B. B. Magnússon, M. Milicevic, A. N. Davidsson, C.-H. Cheng. "Rethinking Code Review Workflows with LLM Assistance: An Empirical Study." arXiv 2505.16339, 2025. https://arxiv.org/abs/2505.16339

[40] K. Sun, Y. Guan, J. Sun, H. Kuang, G. Rong, D. Shao, H. Zhang, X. Ma, C. Treude. "Improving LLM-Based Go Code Review through Issue-List Generation and Context Augmentation." arXiv 2606.01859, 2026. https://arxiv.org/abs/2606.01859

[41] Z. Zhang, Y. Wang, C. Wang, J. Chen, Z. Zheng. "LLM Hallucinations in Practical Code Generation: Phenomena, Mechanism, and Mitigation." ISSTA 2025. https://arxiv.org/abs/2409.20550

[42] S. Dhuliawala, M. Komeili, J. Xu, R. Raileanu, X. Li, A. Celikyilmaz, J. Weston. "Chain-of-Verification Reduces Hallucination in Large Language Models." Findings of ACL 2024. https://aclanthology.org/2024.findings-acl.212/

[43] X. Wang, J. Wei, D. Schuurmans, Q. Le, E. Chi, S. Narang, A. Chowdhery, D. Zhou. "Self-Consistency Improves Chain of Thought Reasoning in Language Models." ICLR 2023. https://arxiv.org/abs/2203.11171

[44] Z. Li, S. Dutta, M. Naik. "IRIS: LLM-Assisted Static Analysis for Detecting Security Vulnerabilities." ICLR 2025. https://arxiv.org/abs/2405.17238

[45] X. Du, J. Feng, Y. Zou, W. Xu, J. Ma, W. Zhang, S. Liu, X. Peng, Y. Lou. "Reducing False Positives in Static Bug Detection with LLMs: An Empirical Study in Industry." ICSE-SEIP 2026. https://arxiv.org/abs/2601.18844

[46] X. Du, K. Yu, C. Wang, Y. Zou, W. Deng, Z. Ou, X. Peng, L. Zhang, Y. Lou. "Minimizing False Positives in Static Bug Detection via LLM-Enhanced Path Feasibility Analysis." arXiv 2506.10322, 2025. https://arxiv.org/abs/2506.10322

[47] Y. Xiong, T. Zhang. "Sifting the Noise: A Comparative Study of LLM Agents in Vulnerability False Positive Filtering." ISSTA 2026. https://arxiv.org/abs/2601.22952

[48] N. Wadhwa, J. Pradhan, A. Sonwane, S. P. Sahu, N. Natarajan, A. Kanade, S. Parthasarathy, S. Rajamani. "CORE: Resolving Code Quality Issues using LLMs." FSE 2024. https://arxiv.org/abs/2309.12938 (arXiv v1 title: "Frustrated with Code Quality Issues? LLMs can Help!")

[49] C. Spiess, D. Gros, K. S. Pai, M. Pradel, M. R. I. Rabin, A. Alipour, S. Jha, P. Devanbu, T. Ahmed. "Calibration and Correctness of Language Models for Code." ICSE 2025. https://arxiv.org/abs/2402.02047

[50] S. Yao, J. Zhao, D. Yu, N. Du, I. Shafran, K. Narasimhan, Y. Cao. "ReAct: Synergizing Reasoning and Acting in Language Models." ICLR 2023. https://arxiv.org/abs/2210.03629

[51] J. Yang, C. E. Jimenez, A. Wettig, K. Lieret, S. Yao, K. Narasimhan, O. Press. "SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering." NeurIPS 2024. https://arxiv.org/abs/2405.15793

[52] Y. Zhang, H. Ruan, Z. Fan, A. Roychoudhury. "AutoCodeRover: Autonomous Program Improvement." ISSTA 2024. https://arxiv.org/abs/2404.05427

[53] X. Wang, B. Li, Y. Song, F. F. Xu, X. Tang, et al. "OpenHands: An Open Platform for AI Software Developers as Generalist Agents." ICLR 2025. https://arxiv.org/abs/2407.16741

[54] C. S. Xia, Y. Deng, S. Dunn, L. Zhang. "Agentless: Demystifying LLM-based Software Engineering Agents." arXiv 2407.01489, 2024 (published at FSE 2025). https://arxiv.org/abs/2407.01489

[55] S. Kapoor, B. Stroebl, Z. S. Siegel, N. Nadgir, A. Narayanan. "AI Agents That Matter." arXiv 2407.01502, 2024. https://arxiv.org/abs/2407.01502

[56] Z. Gao, M. Muñoz Barón, U. Habiba, D. Graziotin, S. Wagner. "Evaluating the Impact of Explainable AI on Trust in AI-Assisted Code Review." ISSTA 2026 (PACMSE). https://arxiv.org/abs/2607.24601

[57] B. Johnson, Y. Song, E. Murphy-Hill, R. Bowdidge. "Why Don't Software Developers Use Static Analysis Tools to Find Bugs?" ICSE 2013. https://doi.org/10.1109/ICSE.2013.6606613

[58] C. Sadowski, E. Aftandilian, A. Eagle, L. Miller-Cushon, C. Jaspan. "Lessons from Building Static Analysis Tools at Google." Communications of the ACM 61(4), 2018. https://doi.org/10.1145/3188720

[59] E. J. Hu, Y. Shen, P. Wallis, Z. Allen-Zhu, Y. Li, S. Wang, L. Wang, W. Chen. "LoRA: Low-Rank Adaptation of Large Language Models." ICLR 2022. https://arxiv.org/abs/2106.09685

[60] T. Dettmers, A. Pagnoni, A. Holtzman, L. Zettlemoyer. "QLoRA: Efficient Finetuning of Quantized LLMs." NeurIPS 2023. https://arxiv.org/abs/2305.14314

[61] M. A. Haider, A. B. Mostofa, S. S. B. Mosaddek, A. Iqbal, T. Ahmed. "Prompting and Fine-tuning Large Language Models for Automated Code Review Comment Generation." arXiv 2411.10129, 2024. https://arxiv.org/abs/2411.10129

[62] I. Begolli, M. Aksoy, D. Neider. "Fine-Tuning Multilingual Language Models for Code Review: An Empirical Study on Industrial C# Projects." arXiv 2507.19271, 2025. https://arxiv.org/abs/2507.19271

[63] O. Ovadia, M. Brief, M. Mishaeli, O. Elisha. "Fine-Tuning or Retrieval? Comparing Knowledge Injection in LLMs." EMNLP 2024. https://arxiv.org/abs/2312.05934

[64] H. Hong, J. Baik. "Retrieval-Augmented Code Review Comment Generation." arXiv 2506.11591, 2025. https://arxiv.org/abs/2506.11591

[65] B. Hui, J. Yang, Z. Cui, J. Yang, D. Liu, L. Zhang, et al. "Qwen2.5-Coder Technical Report." arXiv 2409.12186, 2024. https://arxiv.org/abs/2409.12186

[66] J. Kumar, S. Chimalakonda. "Code Review Automation Via Multi-task Federated LLM: An Empirical Study." arXiv 2412.15676, 2024. https://arxiv.org/abs/2412.15676

[67] Amazon Web Services. "SaaS Tenant Isolation Strategies: Isolating Resources in a Multi-Tenant Environment." AWS Whitepaper, 2020. https://docs.aws.amazon.com/whitepapers/latest/saas-tenant-isolation-strategies/saas-tenant-isolation-strategies.html

[68] The PostgreSQL Global Development Group. "Row Security Policies" (PostgreSQL Documentation, §5.9 in current releases). https://www.postgresql.org/docs/current/ddl-rowsecurity.html

[69] OWASP Foundation. "Application Security Verification Standard (ASVS) 5.0.0." May 2025. https://owasp.org/www-project-application-security-verification-standard/

[70] R. Dror, G. Baumer, S. Shlomov, R. Reichart. "The Hitchhiker's Guide to Testing Statistical Significance in Natural Language Processing." ACL 2018. https://aclanthology.org/P18-1128/ (see also B. Efron, R. J. Tibshirani, *An Introduction to the Bootstrap*, Chapman & Hall, 1993)
