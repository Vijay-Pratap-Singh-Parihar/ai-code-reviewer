import { describe, expect, it, vi, beforeEach } from "vitest";
import { getAuthState, setAuthState, subscribeAuthState } from "@/lib/auth-store";

describe("auth-store", () => {
  beforeEach(() => {
    setAuthState({ accessToken: null, user: null, status: "loading" });
  });

  it("starts in the loading state with no token or user", () => {
    expect(getAuthState()).toEqual({ accessToken: null, user: null, status: "loading" });
  });

  it("merges partial updates instead of replacing the whole state", () => {
    setAuthState({ status: "authenticated" });
    setAuthState({ accessToken: "tok" });

    expect(getAuthState()).toEqual({ accessToken: "tok", user: null, status: "authenticated" });
  });

  it("notifies every subscriber on each update, and stops after unsubscribe", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeAuthState(listener);

    setAuthState({ status: "authenticated" });
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    setAuthState({ status: "unauthenticated" });
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
