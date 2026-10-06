"use client";

import { useQueries } from "@tanstack/react-query";
import { getAnalysisRun, type AnalysisRunPublic } from "@/lib/api-client";
import { Card, CardContent } from "@/components/ui/card";

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-1">
        <span className="text-2xl font-semibold tabular-nums">{value}</span>
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
      </CardContent>
    </Card>
  );
}

/**
 * Reads the same `["analysis-run", runId]` queries each `RunStatusCard`
 * already polls (TanStack Query shares the cache, so this doesn't add any
 * extra network requests) and aggregates them into a stat row. Explicitly
 * "session" analytics, not persistent history — there's no "list my runs"
 * backend endpoint yet (Stage 9 part 2's own deliberate simplification),
 * so this can only ever reflect runs triggered in the current browser tab.
 */
export function AnalyticsWidgets({ runIds }: { runIds: string[] }) {
  const results = useQueries({
    queries: runIds.map((runId) => ({
      queryKey: ["analysis-run", runId],
      queryFn: () => getAnalysisRun(runId),
    })),
  });

  const runs = results
    .map((result) => result.data)
    .filter((run): run is AnalysisRunPublic => run !== undefined);

  const succeeded = runs.filter((run) => run.status === "succeeded").length;
  const failed = runs.filter((run) => run.status === "failed").length;
  const inProgress = runs.filter((run) => run.status === "queued" || run.status === "running").length;
  const totalCost = runs.reduce((sum, run) => sum + run.cost_usd, 0);
  const totalFindings = runs.reduce((sum, run) => sum + run.findings.length, 0);

  return (
    <div className="flex flex-col gap-2">
      <h2 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        Session analytics
      </h2>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatTile label="Runs" value={String(runIds.length)} />
        <StatTile label="Succeeded" value={String(succeeded)} />
        <StatTile label="Failed" value={String(failed)} />
        <StatTile label="In progress" value={String(inProgress)} />
        <StatTile label="Findings" value={String(totalFindings)} />
        <StatTile label="Cost" value={`$${totalCost.toFixed(4)}`} />
      </div>
    </div>
  );
}
