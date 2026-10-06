"use client";

import { useState } from "react";
import { TriggerIndexForm } from "@/components/trigger-index-form";
import { TriggerAnalysisForm } from "@/components/trigger-analysis-form";
import { RunStatusCard } from "@/components/run-status-card";
import { AnalyticsWidgets } from "@/components/analytics-widgets";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function DashboardPage() {
  const [runIds, setRunIds] = useState<string[]>([]);

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-lg font-semibold">Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          There&apos;s no GitHub integration yet (Stage 10), so a review is triggered by pasting a diff
          directly against the real API below.
        </p>
      </div>

      <AnalyticsWidgets runIds={runIds} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>1. Build a branch index</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="mb-3 text-sm text-muted-foreground">
              Only needed before a <code className="font-mono">cross_file</code> review — skip this if
              you&apos;re using the default, cheaper <code className="font-mono">diff_only</code> review.
            </p>
            <TriggerIndexForm />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>2. Trigger a review</CardTitle>
          </CardHeader>
          <CardContent>
            <TriggerAnalysisForm onTriggered={(runId) => setRunIds((ids) => [runId, ...ids])} />
          </CardContent>
        </Card>
      </div>

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
