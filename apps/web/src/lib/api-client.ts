import { getAuthState, setAuthState } from "@/lib/auth-store";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type UserPublic = {
  id: string;
  org_id: string;
  email: string;
  role: string;
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

export function signup(input: {
  org_name: string;
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
};

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

export type InstallationPublic = {
  id: string;
  installation_id: number;
  account_login: string;
  account_type: string;
  installed_at: string;
  repository_count: number;
};

export type RepositoryPublic = {
  id: string;
  full_name: string;
  default_branch: string;
  is_active: boolean;
  connected: boolean;
  auto_review_enabled: boolean;
  github_repo_id: number | null;
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
