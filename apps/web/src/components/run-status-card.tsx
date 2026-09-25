"use client";

import { useQuery } from "@tanstack/react-query";
import { getAnalysisRun, type AnalysisRunStatus } from "@/lib/api-client";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";

const STATUS_BADGE: Record<AnalysisRunStatus, { variant: BadgeVariant; className?: string }> = {
  queued: { variant: "secondary" },
  running: { variant: "default" },
  succeeded: {
    variant: "outline",
    className: "border-emerald-600/40 text-emerald-700 dark:text-emerald-400",
  },
  failed: { variant: "destructive" },
};

const SEVERITY_BADGE: Record<string, { variant: BadgeVariant; className?: string }> = {
  low: { variant: "secondary" },
  medium: { variant: "outline" },
  high: {
    variant: "outline",
    className: "border-amber-600/40 text-amber-700 dark:text-amber-400",
  },
  critical: { variant: "destructive" },
};

const POLL_INTERVAL_MS = 1500;

/**
 * Polls one run until it settles, then stops — TanStack Query's
 * `refetchInterval` callback sees the latest fetched data each tick, so it
 * can decide per-run whether to keep polling without any component state.
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
    <Card>
      <CardContent className="flex flex-col gap-3">
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
          </div>
        )}

        {data?.error && <p className="text-sm text-destructive">{data.error}</p>}

        {data && data.findings.length > 0 && (
          <ul className="flex flex-col gap-2">
            {data.findings.map((finding, index) => {
              const severityBadge = SEVERITY_BADGE[finding.severity] ?? SEVERITY_BADGE.low;
              return (
                <li key={index} className="rounded-md border p-2 text-sm">
                  <div className="flex items-center gap-2">
                    <Badge variant={severityBadge.variant} className={severityBadge.className}>
                      {finding.severity}
                    </Badge>
                    <span className="font-mono text-xs text-muted-foreground">
                      {finding.file_path}:{finding.line_start}-{finding.line_end}
                    </span>
                  </div>
                  <p className="mt-1">{finding.message}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    confidence {finding.confidence.toFixed(2)} &middot; {finding.agent_name}
                  </p>
                </li>
              );
            })}
          </ul>
        )}

        {data?.status === "succeeded" && data.findings.length === 0 && (
          <p className="text-sm text-muted-foreground">No findings.</p>
        )}
      </CardContent>
    </Card>
  );
}
