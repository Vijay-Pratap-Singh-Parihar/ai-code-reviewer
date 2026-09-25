"use client";

import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthBootstrap } from "@/components/auth-provider";

let browserQueryClient: QueryClient | undefined;

function getQueryClient(): QueryClient {
  // Keep server renders isolated from each other, but reuse one client
  // across browser re-renders so the cache actually persists.
  if (typeof window === "undefined") return new QueryClient();
  browserQueryClient ??= new QueryClient();
  return browserQueryClient;
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={getQueryClient()}>
      <AuthBootstrap />
      {children}
    </QueryClientProvider>
  );
}
