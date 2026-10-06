"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { getAnalysisRun } from "@/lib/api-client";
import { STATUS_BADGE } from "@/lib/badges";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

const POLL_INTERVAL_MS = 1500;

/**
 * A compact summary on the dashboard — polls one run until it settles, then
 * stops (`refetchInterval` reads the latest fetched status each tick and
 * returns `false` once terminal). The full diff, findings, and evidence
 * trail render on the dedicated `/runs/[runId]` page this links to, not
 * here, so that rendering logic exists in exactly one place.
 */
export function RunStatusCard({ runId }: { runId: string }) {
  const { data, isError } = useQuery({
    queryKey: ["analysis-run", runId],
    queryFn: () => getAnalysisRun(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "succeeded" || status === "failed" ? false : POLL_INTERVAL_MS;
    },
  });

  if (isError) {
    return (
      <Card>
        <CardContent className="text-sm text-destructive">Failed to load run {runId}.</CardContent>
      </Card>
    );
  }

  const status = data?.status ?? "queued";
  const statusBadge = STATUS_BADGE[status];

  return (
    <Link href={`/runs/${runId}`} className="block">
      <Card className="transition-colors hover:bg-muted/50">
        <CardContent className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            <span className="font-mono text-xs text-muted-foreground">{runId}</span>
            <Badge variant={statusBadge.variant} className={statusBadge.className}>
              {status}
            </Badge>
          </div>

          {data && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>
                {data.tokens_in} in / {data.tokens_out} out tokens
              </span>
              <span>${data.cost_usd.toFixed(4)}</span>
              {data.latency_ms !== null && <span>{data.latency_ms}ms</span>}
              {data.status === "succeeded" && (
                <span>
                  {data.findings.length} finding{data.findings.length === 1 ? "" : "s"}
                </span>
              )}
            </div>
          )}
          {data?.error && <p className="text-sm text-destructive">{data.error}</p>}
        </CardContent>
      </Card>
    </Link>
  );
}
