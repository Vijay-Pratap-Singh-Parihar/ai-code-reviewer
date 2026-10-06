"use client";

import { RepositoryList } from "@/components/repository-list";
import { Card, CardContent } from "@/components/ui/card";

export default function RepositoriesPage() {
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-lg font-semibold">Repositories</h1>
        <p className="text-sm text-muted-foreground">
          Repositories from your connected GitHub accounts. Auto-review is off for every repository until
          you turn it on.
        </p>
      </div>
      <Card>
        <CardContent>
          <RepositoryList />
        </CardContent>
      </Card>
    </div>
  );
}
