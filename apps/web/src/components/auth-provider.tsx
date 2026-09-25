"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";
import * as api from "@/lib/api-client";
import { getAuthState, setAuthState, subscribeAuthState } from "@/lib/auth-store";

/**
 * Attempts one silent refresh on app start so a page reload (or a fresh tab)
 * recovers the session from the httpOnly refresh cookie instead of bouncing
 * straight to /login. Rendered once from `Providers`, above every route.
 */
export function AuthBootstrap() {
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const refreshed = await api.tryRefresh();
      if (cancelled) return;
      if (refreshed) {
        setAuthState({
          accessToken: refreshed.access_token,
          user: refreshed.user,
          status: "authenticated",
        });
      } else {
        setAuthState({ accessToken: null, user: null, status: "unauthenticated" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return null;
}

export function useAuth() {
  const state = useSyncExternalStore(subscribeAuthState, getAuthState, getAuthState);

  const login = useCallback(async (email: string, password: string) => {
    const result = await api.login({ email, password });
    setAuthState({ accessToken: result.access_token, user: result.user, status: "authenticated" });
  }, []);

  const signup = useCallback(async (orgName: string, email: string, password: string) => {
    const result = await api.signup({ org_name: orgName, email, password });
    setAuthState({ accessToken: result.access_token, user: result.user, status: "authenticated" });
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setAuthState({ accessToken: null, user: null, status: "unauthenticated" });
    }
  }, []);

  return { ...state, login, signup, logout };
}
