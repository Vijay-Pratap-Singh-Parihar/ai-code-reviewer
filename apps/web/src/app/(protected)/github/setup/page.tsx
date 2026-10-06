"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { GitHubSetupHandler } from "@/components/github-setup-handler";

function SetupFromSearchParams() {
  const params = useSearchParams();
  return (
    <GitHubSetupHandler
      installationId={params.get("installation_id")}
      code={params.get("code")}
      setupAction={params.get("setup_action")}
    />
  );
}

export default function GitHubSetupPage() {
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-4 p-6">
      <h1 className="text-lg font-semibold">Connecting GitHub</h1>
      {/* useSearchParams needs a Suspense boundary for static prerendering. */}
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
        <SetupFromSearchParams />
      </Suspense>
    </div>
  );
}
