"use client";

import { HistoryView } from "@/components/history/history-view";

export default function HistoryPage() {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-lg font-semibold">History</h1>
        <p className="text-sm text-muted-foreground">
          Every review your organization has run, newest first, including pull requests that have since been merged or
          closed. Open a review to see its findings and evidence.
        </p>
      </div>
      <HistoryView />
    </div>
  );
}
