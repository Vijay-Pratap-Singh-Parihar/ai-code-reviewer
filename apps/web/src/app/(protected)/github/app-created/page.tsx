"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { GitHubAppCreatedHandler } from "@/components/github-app-created-handler";

function FromSearchParams() {
  const params = useSearchParams();
  return <GitHubAppCreatedHandler code={params.get("code")} state={params.get("state")} />;
}

export default function GitHubAppCreatedPage() {
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-4 p-6">
      <h1 className="text-lg font-semibold">Setting up GitHub</h1>
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
        <FromSearchParams />
      </Suspense>
    </div>
  );
}
