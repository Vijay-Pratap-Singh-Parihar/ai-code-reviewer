"use client";

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { getAnalysisRun } from "@/lib/api-client";
import { useRunMetadata } from "@/lib/run-metadata-store";
import { STATUS_BADGE } from "@/lib/badges";
import { Badge } from "@/components/ui/badge";
import { DiffViewer } from "@/components/diff-viewer";
import { EvidenceTrail } from "@/components/evidence-trail";

const POLL_INTERVAL_MS = 1500;

export default function RunPage() {
  const { runId } = useParams<{ runId: string }>();

  // Client-only (sessionStorage isn't available during SSR) — see
  // run-metadata-store's docstring for why this data lives here at all.
  const metadata = useRunMetadata(runId);

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
      <div className="mx-auto w-full max-w-4xl p-6">
        <p className="text-sm text-destructive">Failed to load run {runId}.</p>
      </div>
    );
  }

  const status = data?.status ?? "queued";
  const statusBadge = STATUS_BADGE[status];

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 p-6">
      <div className="flex flex-col gap-1">
        <Link href="/dashboard" className="text-sm text-muted-foreground underline underline-offset-4">
          &larr; Back to dashboard
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-lg font-semibold">
            {metadata ? metadata.prTitle : `Run ${runId}`}
          </h1>
          <Badge variant={statusBadge.variant} className={statusBadge.className}>
            {status}
          </Badge>
        </div>
        {metadata && (
          <p className="text-sm text-muted-foreground">
            {metadata.repoFullName} &middot; {metadata.baseBranch} &middot; PR #{metadata.prNumber} &middot;{" "}
            {metadata.agent}
          </p>
        )}
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
      </div>

      <div className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-muted-foreground">Diff</h2>
        {metadata ? (
          <DiffViewer diffText={metadata.diff} findings={data?.findings ?? []} />
        ) : (
          <p className="text-sm text-muted-foreground">
            The diff isn&apos;t available — it isn&apos;t stored on the server, only kept in this
            browser tab for the session this run was triggered in (see the dashboard&apos;s trigger
            form). Findings and their evidence trail still work below.
          </p>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-muted-foreground">Findings &amp; evidence trail</h2>
        <EvidenceTrail findings={data?.findings ?? []} />
      </div>
    </div>
  );
}
