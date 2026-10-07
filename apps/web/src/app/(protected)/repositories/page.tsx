"use client";

import { RepositoryGroups } from "@/components/repository-groups";

export default function RepositoriesPage() {
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-lg font-semibold">Repositories</h1>
        <p className="text-sm text-muted-foreground">
          Repositories from your connected GitHub accounts, grouped by account. Auto-review is off for every
          repository until an owner or admin turns it on.
        </p>
      </div>
      <RepositoryGroups />
    </div>
  );
}
