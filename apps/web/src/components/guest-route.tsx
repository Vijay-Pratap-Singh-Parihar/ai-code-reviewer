"use client";

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/auth-provider";
import { postLoginPath } from "@/lib/redirect";

/** Wraps a route (login, signup) meant only for a signed-out visitor. */
export function GuestRoute({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "authenticated") router.replace(postLoginPath());
  }, [status, router]);

  if (status === "authenticated") return null;
  return <>{children}</>;
}
