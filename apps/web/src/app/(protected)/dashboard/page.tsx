"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { TriggerAnalysisForm } from "@/components/trigger-analysis-form";
import { RunStatusCard } from "@/components/run-status-card";
import { AnalyticsWidgets } from "@/components/analytics-widgets";
import { RepositoryList } from "@/components/repository-list";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function DashboardPage() {
  const [runIds, setRunIds] = useState<string[]>([]);

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-lg font-semibold">Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          Review pull requests from your connected GitHub repositories.
        </p>
      </div>

      <AnalyticsWidgets runIds={runIds} />

      <Card>
        <CardHeader>
          <CardTitle>Repositories</CardTitle>
        </CardHeader>
        <CardContent>
          <RepositoryList limit={5} compact />
        </CardContent>
      </Card>

      {/* The original Stage 3–9 trigger: still useful without a GitHub App,
          or for reviewing a diff that isn't a PR. Collapsed by default. */}
      <details className="group rounded-xl border bg-card">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-medium [&::-webkit-details-marker]:hidden">
          <ChevronRight className="size-4 transition-transform group-open:rotate-90" />
          Advanced: review a pasted diff
          <span className="font-normal text-muted-foreground">(no GitHub connection needed)</span>
        </summary>
        <div className="p-4 pt-0">
          <Card>
            <CardHeader>
              <CardTitle>Trigger a review</CardTitle>
            </CardHeader>
            <CardContent>
              <TriggerAnalysisForm onTriggered={(runId) => setRunIds((ids) => [runId, ...ids])} />
            </CardContent>
          </Card>
        </div>
      </details>

      {runIds.length > 0 && (
        <div className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold text-muted-foreground">Runs this session</h2>
          {runIds.map((runId) => (
            <RunStatusCard key={runId} runId={runId} />
          ))}
        </div>
      )}
    </div>
  );
}
