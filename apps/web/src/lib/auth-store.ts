import type { UserPublic } from "@/lib/api-client";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

export type AuthState = {
  accessToken: string | null;
  user: UserPublic | null;
  status: AuthStatus;
};

/**
 * The access token lives here — a plain module-level variable, not
 * `localStorage`/`sessionStorage` — so it's never readable by an injected
 * script surviving an XSS bug. It doesn't survive a page reload; recovering
 * it after one is what the silent `tryRefresh()` call on app start is for
 * (the httpOnly refresh cookie does survive a reload).
 */
let state: AuthState = { accessToken: null, user: null, status: "loading" };

const listeners = new Set<() => void>();

export function getAuthState(): AuthState {
  return state;
}

export function setAuthState(partial: Partial<AuthState>): void {
  state = { ...state, ...partial };
  for (const listener of listeners) listener();
}

export function subscribeAuthState(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
