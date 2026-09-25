import { describe, expect, it, vi, beforeEach } from "vitest";
import { apiFetch, apiFetchJson, ApiError, formatApiError, login, signup } from "@/lib/api-client";
import { getAuthState, setAuthState } from "@/lib/auth-store";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiFetch", () => {
  beforeEach(() => {
    setAuthState({ accessToken: null, user: null, status: "loading" });
    vi.restoreAllMocks();
  });

  it("attaches the access token as a bearer header when one is set", async () => {
    setAuthState({ accessToken: "tok-1", status: "authenticated" });
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/analysis");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-1");
  });

  it("does not attempt a refresh on a 401 when no token was attached (e.g. bad login)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(401, { detail: "invalid email or password" }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await apiFetch("/auth/login", { method: "POST" });

    expect(response.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(1); // no refresh attempt, no retry
  });

  it("retries once via a silent refresh when an authenticated request gets a 401", async () => {
    setAuthState({ accessToken: "expired", user: null, status: "authenticated" });
    const fetchMock = vi
      .fn()
      // original request: expired token
      .mockResolvedValueOnce(jsonResponse(401, { detail: "expired" }))
      // refresh call succeeds
      .mockResolvedValueOnce(
        jsonResponse(200, {
          access_token: "fresh",
          token_type: "bearer",
          expires_in: 900,
          user: { id: "u1", org_id: "o1", email: "a@b.com", role: "owner" },
        }),
      )
      // retried original request succeeds
      .mockResolvedValueOnce(jsonResponse(200, { data: "ok" }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await apiFetch("/analysis");

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[1][0]).toContain("/auth/refresh");
    expect(await response.json()).toEqual({ data: "ok" });
    expect(getAuthState()).toMatchObject({ accessToken: "fresh", status: "authenticated" });
  });

  it("signs the session out when the refresh itself fails", async () => {
    setAuthState({ accessToken: "expired", user: null, status: "authenticated" });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(401, { detail: "expired" }))
      .mockResolvedValueOnce(jsonResponse(401, { detail: "missing refresh token" }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await apiFetch("/analysis");

    expect(response.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(2); // no third (retried) call
    expect(getAuthState()).toEqual({ accessToken: null, user: null, status: "unauthenticated" });
  });
});

describe("apiFetchJson", () => {
  beforeEach(() => {
    setAuthState({ accessToken: null, user: null, status: "loading" });
    vi.restoreAllMocks();
  });

  it("returns the parsed body on success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, { hello: "world" })));
    await expect(apiFetchJson("/anything")).resolves.toEqual({ hello: "world" });
  });

  it("throws an ApiError carrying the response's detail on failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(409, { detail: "repository already registered" })),
    );

    await expect(apiFetchJson("/analysis")).rejects.toMatchObject({
      status: 409,
      detail: "repository already registered",
    });
  });
});

describe("signup/login request shapes", () => {
  beforeEach(() => {
    setAuthState({ accessToken: null, user: null, status: "loading" });
    vi.restoreAllMocks();
  });

  it("signup posts org_name/email/password to /auth/signup", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(201, {
        access_token: "t",
        token_type: "bearer",
        expires_in: 900,
        user: { id: "u1", org_id: "o1", email: "a@b.com", role: "owner" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await signup({ org_name: "Acme", email: "a@b.com", password: "correct-horse-battery" });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/auth/signup");
    expect(JSON.parse(init.body as string)).toEqual({
      org_name: "Acme",
      email: "a@b.com",
      password: "correct-horse-battery",
    });
  });

  it("login posts email/password to /auth/login", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, {
        access_token: "t",
        token_type: "bearer",
        expires_in: 900,
        user: { id: "u1", org_id: "o1", email: "a@b.com", role: "owner" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await login({ email: "a@b.com", password: "correct-horse-battery" });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/auth/login");
    expect(JSON.parse(init.body as string)).toEqual({
      email: "a@b.com",
      password: "correct-horse-battery",
    });
  });
});

describe("formatApiError", () => {
  it("uses a string detail directly", () => {
    expect(formatApiError(new ApiError(409, "already registered"))).toBe("already registered");
  });

  it("joins Pydantic-style validation error messages", () => {
    const err = new ApiError(422, [{ msg: "field required" }, { msg: "too short" }]);
    expect(formatApiError(err)).toBe("field required; too short");
  });

  it("falls back to a generic message for a non-ApiError", () => {
    expect(formatApiError(new TypeError("network down"))).toBe("network down");
    expect(formatApiError("nonsense")).toBe("Something went wrong. Please try again.");
  });
});
