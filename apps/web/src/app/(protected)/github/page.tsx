"use client";

import { GitHubAccounts } from "@/components/github-accounts";

export default function GitHubPage() {
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-lg font-semibold">GitHub</h1>
        <p className="text-sm text-muted-foreground">
          Connected GitHub accounts and the repositories revu can read.
        </p>
      </div>
      <GitHubAccounts />
    </div>
  );
}
