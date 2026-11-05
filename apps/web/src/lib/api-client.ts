import { getAuthState, setAuthState } from "@/lib/auth-store";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type UserRole = "owner" | "admin" | "member";

export type UserPublic = {
  id: string;
  org_id: string;
  email: string;
  // Null only for accounts created before names were asked for.
  first_name?: string | null;
  last_name?: string | null;
  role: UserRole | string;
  // Deployment operator: the only role that may create the GitHub App.
  is_platform_admin?: boolean;
};

export type AccessTokenResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserPublic;
};

/**
 * Thrown for any non-2xx API response. `detail` mirrors FastAPI's error
 * body shape (a string for a plain HTTPException, an array of Pydantic
 * validation errors for a 422).
 */
export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : `API request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function parseJsonSafely(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function rawRequest(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init.headers },
  });
}

// The refresh_token cookie is scoped to the /auth path server-side, so this
// call is the only one that ever needs to send it; every other request
// authenticates with the short-lived access token in the Authorization header.
let refreshPromise: Promise<AccessTokenResponse | null> | null = null;

export async function tryRefresh(): Promise<AccessTokenResponse | null> {
  // Concurrent callers (e.g. two API calls racing a 401 at once) share one
  // in-flight refresh instead of each rotating the refresh token themselves,
  // which would make the loser's rotated cookie invalid.
  if (refreshPromise === null) {
    refreshPromise = (async () => {
      const response = await rawRequest("/auth/refresh", { method: "POST" });
      if (!response.ok) return null;
      return (await response.json()) as AccessTokenResponse;
    })().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

/**
 * Fetch wrapper that attaches the in-memory access token and transparently
 * retries once via a silent refresh on a 401 — but only when a token was
 * actually attached, so a genuine "wrong password" 401 from /auth/login
 * isn't mistaken for an expired-token 401.
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const accessToken = getAuthState().accessToken;
  const response = await rawRequest(path, {
    ...init,
    headers: {
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...init.headers,
    },
  });

  if (response.status !== 401 || accessToken === null) {
    return response;
  }

  const refreshed = await tryRefresh();
  if (refreshed === null) {
    setAuthState({ accessToken: null, user: null, status: "unauthenticated" });
    return response;
  }

  setAuthState({ accessToken: refreshed.access_token, user: refreshed.user, status: "authenticated" });
  return rawRequest(path, {
    ...init,
    headers: { Authorization: `Bearer ${refreshed.access_token}`, ...init.headers },
  });
}

export async function apiFetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  const body = await parseJsonSafely(response);
  if (!response.ok) {
    const detail = body !== null && typeof body === "object" && "detail" in body
      ? (body as { detail: unknown }).detail
      : body;
    throw new ApiError(response.status, detail);
  }
  return body as T;
}

/** "Ada Lovelace", or the email for an account that has no name yet. */
export function displayName(user: Pick<UserPublic, "email" | "first_name" | "last_name">): string {
  const name = [user.first_name, user.last_name].filter(Boolean).join(" ").trim();
  return name || user.email;
}

export function updateProfile(input: { first_name: string; last_name: string }): Promise<UserPublic> {
  return apiFetchJson<UserPublic>("/auth/me", { method: "PATCH", body: JSON.stringify(input) });
}

export function signup(input: {
  org_name: string;
  first_name: string;
  last_name: string;
  email: string;
  password: string;
}): Promise<AccessTokenResponse> {
  return apiFetchJson<AccessTokenResponse>("/auth/signup", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function login(input: { email: string; password: string }): Promise<AccessTokenResponse> {
  return apiFetchJson<AccessTokenResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function logout(): Promise<void> {
  await apiFetch("/auth/logout", { method: "POST" });
}

export type Agent = "diff_only" | "cross_file";

export type EvidenceItem = {
  file_path: string;
  line_start: number;
  line_end: number;
  reason: string;
};

export type FindingPublic = {
  file_path: string;
  line_start: number;
  line_end: number;
  category: string;
  severity: "low" | "medium" | "high" | "critical";
  message: string;
  confidence: number;
  agent_name: string;
  evidence: EvidenceItem[];
};

export type AnalysisRunStatus = "queued" | "running" | "succeeded" | "failed";

export type AnalysisRunPublic = {
  id: string;
  status: AnalysisRunStatus;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  latency_ms: number | null;
  error: string | null;
  findings: FindingPublic[];
  // PR context, stored server-side since Stage 10. Null for runs created
  // before then (the run page falls back to run-metadata-store for those).
  agent?: Agent | null;
  repo_full_name?: string | null;
  pr_number?: number | null;
  pr_title?: string | null;
  base_branch?: string | null;
  head_sha?: string | null;
  diff?: string | null;
  // "github" for pull request reviews (postable back to the PR), "manual" for pasted diffs.
  source?: string | null;
  github_review_url?: string | null;
  posted_at?: string | null;
  post_error?: string | null;
  /** How the review went: files left out, parts, a model that stopped short. */
  notices?: string[];
  /** The failed run this one retries, when it is a retry. */
  retry_of?: string | null;
};

/** Run a failed review again as a new run; answers with the new run. */
export function retryAnalysis(runId: string): Promise<AnalysisRunPublic> {
  return apiFetchJson<AnalysisRunPublic>(`/analysis/${runId}/retry`, { method: "POST" });
}

/** Queue posting a finished PR review to GitHub; a no-op once it is posted. */
export function postReviewToGitHub(runId: string): Promise<AnalysisRunPublic> {
  return apiFetchJson<AnalysisRunPublic>(`/analysis/${runId}/github-review`, { method: "POST" });
}

export type AnalysisRequest = {
  repo_full_name: string;
  base_branch?: string;
  head_sha: string;
  pr_number: number;
  pr_title: string;
  pr_body?: string;
  diff: string;
  // Pasted diffs are always reviewed diff-only: cross-file review reads the
  // repository, which only GitHub-connected repos give the worker.
  agent?: "diff_only";
};

export function triggerAnalysis(body: AnalysisRequest): Promise<AnalysisRunPublic> {
  return apiFetchJson<AnalysisRunPublic>("/analysis", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getAnalysisRun(runId: string): Promise<AnalysisRunPublic> {
  return apiFetchJson<AnalysisRunPublic>(`/analysis/${runId}`);
}

export type BranchIndexPublic = {
  id: string;
  repo_id: string;
  branch_name: string;
  head_sha: string | null;
  status: "pending" | "building" | "ready" | "stale" | "failed";
  node_count: number;
  edge_count: number;
  build_duration_ms: number | null;
  built_at: string | null;
  created_at: string;
};

export type BranchIndexStatusPublic = {
  repo_id: string;
  branch_name: string;
  has_ready_index: boolean;
  head_sha: string | null;
  node_count: number;
  edge_count: number;
  unresolved_count: number;
  build_duration_ms: number | null;
  built_at: string | null;
  ready_index_id: string | null;
  is_stale: boolean;
  latest_attempt_status: string | null;
  latest_attempt_id: string | null;
};

export function getBranchIndexStatus(
  repoId: string,
  branchName: string,
): Promise<BranchIndexStatusPublic> {
  return apiFetchJson<BranchIndexStatusPublic>(
    `/repos/${repoId}/branches/${encodeURIComponent(branchName)}/index`,
  );
}

// --- GitHub App (Stage 10) ---------------------------------------------------

export type GitHubAppInfo = {
  configured: boolean;
  install_url: string | null;
  webhook_configured: boolean;
  source?: "env" | "database" | null;
  slug?: string | null;
  app_url?: string | null;
  webhook_proxy_url?: string | null;
  error?: string | null;
};

export type ManifestStart = { action_url: string; manifest: Record<string, unknown> };

/** Step 1 of one-click App creation: what to POST to GitHub. */
export function startAppManifest(organization?: string): Promise<ManifestStart> {
  return apiFetchJson<ManifestStart>("/github/app/manifest", {
    method: "POST",
    body: JSON.stringify({ organization: organization || null }),
  });
}

/** Step 2: trade the code GitHub redirected back with for the new App. */
export function completeAppManifest(body: { code: string; state: string }): Promise<GitHubAppInfo> {
  return apiFetchJson<GitHubAppInfo>("/github/app/conversions", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export type ConnectionStatus = "active" | "suspended" | "uninstalled";

export type InstallationPublic = {
  id: string;
  installation_id: number;
  account_login: string;
  account_type: string;
  installed_at: string;
  repository_count: number;
  status?: ConnectionStatus;
  suspended_at?: string | null;
  uninstalled_at?: string | null;
  // Disconnected repositories under this connection waiting for the
  // retention job, and when the earliest of them is deleted.
  disconnected_repository_count?: number;
  purge_after?: string | null;
  // The installation granted `pull_requests: write`, which posting reviews needs.
  can_post_reviews?: boolean;
};

export type RepositoryPublic = {
  id: string;
  full_name: string;
  default_branch: string;
  is_active: boolean;
  connected: boolean;
  auto_review_enabled: boolean;
  github_repo_id: number | null;
  installation_id?: string | null;
  disconnected_at?: string | null;
  // When the retention job deletes this repository's data, if disconnected.
  purge_after?: string | null;
};

export type PullRequestSummary = {
  number: number;
  title: string;
  author: string;
  head_sha: string;
  base_branch: string;
  draft: boolean;
  html_url: string | null;
  updated_at: string | null;
  latest_run: { id: string; status: AnalysisRunStatus; agent: string; head_sha: string | null } | null;
};

export function getGitHubApp(): Promise<GitHubAppInfo> {
  return apiFetchJson<GitHubAppInfo>("/github/app");
}

export function listInstallations(): Promise<InstallationPublic[]> {
  return apiFetchJson<InstallationPublic[]>("/github/installations");
}

export function linkInstallation(body: { installation_id: number; code: string }): Promise<InstallationPublic> {
  return apiFetchJson<InstallationPublic>("/github/installations", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function syncInstallation(id: string): Promise<InstallationPublic> {
  return apiFetchJson<InstallationPublic>(`/github/installations/${id}/sync`, { method: "POST" });
}

export function listRepositories(): Promise<RepositoryPublic[]> {
  return apiFetchJson<RepositoryPublic[]>("/repos");
}

/** Queue deletion of a disconnected (or never-connected) repository's data. */
export function deleteRepositoryData(repoId: string): Promise<{ status: string }> {
  return apiFetchJson<{ status: string }>(`/repos/${repoId}/data`, { method: "DELETE" });
}

/** Queue deletion of everything an uninstalled connection left behind. */
export function deleteInstallationData(id: string): Promise<{ status: string }> {
  return apiFetchJson<{ status: string }>(`/github/installations/${id}/data`, {
    method: "DELETE",
  });
}

export type AuditEntry = {
  id: string;
  at: string;
  action: string;
  target: string;
  actor_id: string | null;
  actor_email: string | null;
  metadata: Record<string, unknown>;
};

export type AuditPage = { entries: AuditEntry[]; next_before: string | null };

export function listAuditEntries(params: {
  limit?: number;
  before?: string | null;
  action?: string | null;
}): Promise<AuditPage> {
  const query = new URLSearchParams();
  if (params.limit) query.set("limit", String(params.limit));
  if (params.before) query.set("before", params.before);
  if (params.action) query.set("action", params.action);
  const suffix = query.size ? `?${query.toString()}` : "";
  return apiFetchJson<AuditPage>(`/audit${suffix}`);
}

export function updateRepository(
  repoId: string,
  body: { auto_review_enabled: boolean },
): Promise<RepositoryPublic> {
  return apiFetchJson<RepositoryPublic>(`/repos/${repoId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function listPullRequests(repoId: string): Promise<PullRequestSummary[]> {
  return apiFetchJson<PullRequestSummary[]>(`/repos/${repoId}/pulls`);
}

export function reviewPullRequest(
  repoId: string,
  number: number,
  agent: Agent,
): Promise<AnalysisRunPublic> {
  return apiFetchJson<AnalysisRunPublic>(`/repos/${repoId}/pulls/${number}/analysis`, {
    method: "POST",
    body: JSON.stringify({ agent }),
  });
}

export function indexRepository(
  repoId: string,
  body: { branch_name?: string; force_full?: boolean } = {},
): Promise<BranchIndexPublic> {
  return apiFetchJson<BranchIndexPublic>(`/repos/${repoId}/index`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// --- Usage & Budget ----------------------------------------------------------

export type BudgetStatus = "ok" | "warning" | "exceeded";

export type Budget = {
  /** null: no cap of that kind. */
  monthly_budget_usd: number | null;
  token_cap: number | null;
  warn_percent: number;
};

export type UsageTotals = { cost_usd: number; tokens: number; runs: number };

export type Usage = {
  period_start: string;
  resets_at: string;
  spent_usd: number;
  tokens: number;
  runs: number;
  budget: Budget;
  /** Of whichever cap is closer to being reached; null without a cap. */
  used_percent: number | null;
  status: BudgetStatus;
  daily: (UsageTotals & { day: string })[];
  months: (UsageTotals & { month: string })[];
  by_repo: (UsageTotals & { repo_full_name: string })[];
  by_model: (UsageTotals & { provider_name: string; model: string })[];
};

export function getUsage(): Promise<Usage> {
  return apiFetchJson<Usage>("/usage");
}

export function setBudget(body: {
  monthly_budget_usd: string | null;
  token_cap: number | null;
  warn_percent: number;
}): Promise<Budget> {
  return apiFetchJson<Budget>("/usage/budget", { method: "PUT", body: JSON.stringify(body) });
}

// --- History -----------------------------------------------------------------

export type Severity = "low" | "medium" | "high" | "critical";
export type PullRequestState = "open" | "closed" | "merged";

export type HistoryRun = {
  id: string;
  created_at: string;
  finished_at: string | null;
  status: AnalysisRunStatus;
  agent: string | null;
  model: string | null;
  source: string | null;
  trigger: string | null;
  repo_id: string;
  repo_full_name: string;
  pr_number: number;
  pr_title: string;
  pr_state: PullRequestState;
  findings: number;
  severities: Partial<Record<Severity, number>>;
  cost_usd: number;
  tokens: number;
  github_review_url: string | null;
  error: string | null;
};

export type HistoryPage = { runs: HistoryRun[]; next_before: string | null };

export type HistoryFilters = {
  repo_id?: string | null;
  /** Runs with a finding at this severity or above. */
  severity?: Severity | null;
  outcome?: AnalysisRunStatus | null;
  pr_state?: PullRequestState | null;
};

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  return search.size ? `?${search.toString()}` : "";
}

export function listHistory(
  filters: HistoryFilters & { before?: string | null; limit?: number },
): Promise<HistoryPage> {
  return apiFetchJson<HistoryPage>(`/history${query(filters)}`);
}

export type AdoptionStats = {
  runs: number;
  succeeded: number;
  failed: number;
  in_progress: number;
  pull_requests: number;
  merged_pull_requests: number;
  posted_to_github: number;
  findings: number;
  by_severity: Partial<Record<Severity, number>>;
  accepted: number;
  dismissed: number;
  ignored: number;
  no_feedback: number;
  /** accepted / (accepted + dismissed); null before any feedback. */
  acceptance_rate: number | null;
};

export function getAdoptionStats(repoId?: string | null): Promise<AdoptionStats> {
  return apiFetchJson<AdoptionStats>(`/history/stats${query({ repo_id: repoId })}`);
}

// --- Branch Memory -----------------------------------------------------------

export type IndexUpdateLog = {
  mode: "incremental" | "full";
  from_sha: string | null;
  to_sha: string;
  files_changed: number;
  duration_ms: number;
  reason: string | null;
  created_at: string;
};

export type BranchMemoryRow = {
  repo_id: string;
  repo_full_name: string;
  repo_active: boolean;
  branch_name: string;
  is_default_branch: boolean;
  state: "ready" | "building" | "failed" | "not_indexed";
  has_ready_index: boolean;
  head_sha: string | null;
  node_count: number;
  edge_count: number;
  unresolved_count: number;
  unresolved_capped: boolean;
  unresolved_rate: number | null;
  build_duration_ms: number | null;
  built_at: string | null;
  last_update: IndexUpdateLog | null;
  full_rebuilds_30d: number;
  incremental_updates_30d: number;
};

export function getBranchMemory(): Promise<BranchMemoryRow[]> {
  return apiFetchJson<BranchMemoryRow[]>("/branch-memory");
}

/** Renders an `ApiError` (or any thrown value) as one user-facing line. */
export function formatApiError(err: unknown): string {
  if (err instanceof ApiError) {
    if (typeof err.detail === "string") return err.detail;
    if (Array.isArray(err.detail)) {
      return err.detail
        .map((item) =>
          item !== null && typeof item === "object" && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : JSON.stringify(item),
        )
        .join("; ");
    }
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return "Something went wrong. Please try again.";
}

// --- AI providers ------------------------------------------------------------

export type ProviderKind =
  | "anthropic"
  | "openai"
  | "groq"
  | "openai_compatible"
  | "bedrock"
  | "azure"
  | "vertex";

export type ModelTier = "screen" | "review" | "verify";

export type ProviderKindInfo = {
  kind: ProviderKind;
  label: string;
  needs_api_key: boolean;
  needs_base_url: boolean;
  default_base_url: string | null;
  available: boolean;
  /** Common servers of this kind, with the host revu's API can actually reach. */
  presets?: { label: string; url: string }[];
};

export type ProviderSettings = {
  supports_tools?: boolean | null;
  supports_json?: boolean | null;
  context_window?: number | null;
  input_cost_per_mtok?: number | null;
  output_cost_per_mtok?: number | null;
};

/** A provider as the API returns it: never the key, only a hint. */
export type ProviderPublic = {
  id: string;
  name: string;
  kind: ProviderKind;
  kind_label: string;
  base_url: string | null;
  effective_base_url: string | null;
  key_hint: string | null;
  has_api_key: boolean;
  header_names: string[];
  settings: ProviderSettings;
  verified_at: string | null;
  last_test_error: string | null;
  created_at: string;
  updated_at: string | null;
  used_by: ModelTier[];
};

export type ProviderCreate = {
  name: string;
  kind: ProviderKind;
  base_url?: string | null;
  api_key?: string | null;
  headers?: Record<string, string> | null;
  settings?: ProviderSettings;
};

/** Absent `api_key` keeps the stored key; `""` clears it. */
export type ProviderUpdate = Partial<Omit<ProviderCreate, "kind">>;

export type ProbeResult = { ok: boolean; detail: string | null; latency_ms: number | null };

export type ProviderTestResult = {
  ok: boolean;
  model: string;
  reply: ProbeResult;
  json_mode: ProbeResult;
  tool_calling: ProbeResult;
  cost_usd: number;
};

/** How much a model may think before answering; `null` leaves it to the model. */
export type Reasoning = "off" | "low" | "medium" | "high";

export type ModelRoutePublic = {
  tier: ModelTier;
  provider_id: string;
  provider_name: string;
  model: string;
  reasoning: Reasoning | null;
  updated_at: string | null;
};

export type RouteUpdate = Partial<
  Record<ModelTier, { provider_id: string; model: string; reasoning: Reasoning | null } | null>
>;

export function listProviderKinds(): Promise<ProviderKindInfo[]> {
  return apiFetchJson<ProviderKindInfo[]>("/providers/kinds");
}

export function listProviders(): Promise<ProviderPublic[]> {
  return apiFetchJson<ProviderPublic[]>("/providers");
}

export function createProvider(body: ProviderCreate): Promise<ProviderPublic> {
  return apiFetchJson<ProviderPublic>("/providers", { method: "POST", body: JSON.stringify(body) });
}

export function updateProvider(id: string, body: ProviderUpdate): Promise<ProviderPublic> {
  return apiFetchJson<ProviderPublic>(`/providers/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

/** 204 No Content on success; errors carry the API's detail like every call. */
export async function deleteProvider(id: string): Promise<void> {
  const response = await apiFetch(`/providers/${id}`, { method: "DELETE" });
  if (!response.ok) {
    const body = await parseJsonSafely(response);
    const detail =
      body !== null && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : body;
    throw new ApiError(response.status, detail);
  }
}

export function testProvider(
  id: string,
  model: string,
  reasoning: Reasoning | null = null,
): Promise<ProviderTestResult> {
  return apiFetchJson<ProviderTestResult>(`/providers/${id}/test`, {
    method: "POST",
    body: JSON.stringify({ model, reasoning }),
  });
}

export type TestStep = "reply" | "json_mode" | "tool_calling";

export type ProviderTestEvent =
  | { event: "start"; step: TestStep }
  | { event: "step"; step: TestStep; result: ProbeResult }
  | { event: "done"; result: ProviderTestResult };

/**
 * The same connection test, reported as it runs: the API streams one JSON
 * line when each call starts and ends, so the UI can show which one a slow
 * local model is on. Resolves with the recorded result.
 */
export async function testProviderLive(
  id: string,
  body: { model: string; reasoning: Reasoning | null },
  onEvent: (event: ProviderTestEvent) => void,
  signal?: AbortSignal,
): Promise<ProviderTestResult> {
  const response = await apiFetch(`/providers/${id}/test/stream`, {
    method: "POST",
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    const parsed = await parseJsonSafely(response);
    const detail =
      parsed !== null && typeof parsed === "object" && "detail" in parsed
        ? (parsed as { detail: unknown }).detail
        : parsed;
    throw new ApiError(response.status, detail);
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffered = "";
  let result: ProviderTestResult | null = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buffered += value;
    const lines = buffered.split("\n");
    buffered = done ? "" : (lines.pop() ?? "");
    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line) as ProviderTestEvent;
      if (event.event === "done") result = event.result;
      onEvent(event);
    }
    if (done) break;
  }
  if (result === null) throw new Error("The test stopped before it finished. Try again.");
  return result;
}

export function listProviderModels(id: string): Promise<{ models: string[] }> {
  return apiFetchJson<{ models: string[] }>(`/providers/${id}/models`);
}

export function listModelRoutes(): Promise<ModelRoutePublic[]> {
  return apiFetchJson<ModelRoutePublic[]>("/providers/routes");
}

export function setModelRoutes(routes: RouteUpdate): Promise<ModelRoutePublic[]> {
  return apiFetchJson<ModelRoutePublic[]>("/providers/routes", {
    method: "PUT",
    body: JSON.stringify({ routes }),
  });
}
