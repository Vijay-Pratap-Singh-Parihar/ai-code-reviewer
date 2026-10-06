"use client";

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/auth-provider";
import { loginPathReturningHere } from "@/lib/redirect";

/** Wraps a route that requires an authenticated session. */
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "unauthenticated") router.replace(loginPathReturningHere());
  }, [status, router]);

  if (status !== "authenticated") return null;
  return <>{children}</>;
}
