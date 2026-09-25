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
