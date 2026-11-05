"use client";

import { BranchMemoryView } from "@/components/branch-memory/branch-memory-view";

export default function BranchMemoryPage() {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-lg font-semibold">Branch Memory</h1>
        <p className="text-sm text-muted-foreground">
          The code graph built ahead of time for each branch, which cross-file reviews read instead of exploring the
          repository on every run. Pushes update it incrementally; a force-push or rewritten history needs a full
          rebuild. Building uses no AI model.
        </p>
      </div>
      <BranchMemoryView />
    </div>
  );
}
