const DEFAULT_PATH = "/dashboard";

/**
 * Validates a post-login `?next=` target. Only same-origin absolute paths
 * are allowed, so a crafted login link can't bounce a user to another site
 * (`//evil.com` and `/\evil.com` are protocol-relative to browsers).
 */
export function safeNextPath(raw: string | null | undefined): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) {
    return DEFAULT_PATH;
  }
  return raw;
}

/** Where to go after signing in, from the current URL's `?next=`. */
export function postLoginPath(): string {
  if (typeof window === "undefined") return DEFAULT_PATH;
  return safeNextPath(new URLSearchParams(window.location.search).get("next"));
}

/**
 * The login URL that returns here afterwards. Matters most for the GitHub
 * setup callback, whose single-use `code` would be lost by a bare redirect.
 */
export function loginPathReturningHere(): string {
  if (typeof window === "undefined") return "/login";
  const here = window.location.pathname + window.location.search;
  return here === DEFAULT_PATH || here === "/" ? "/login" : `/login?next=${encodeURIComponent(here)}`;
}
