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

  // Fallback only: runs created before Stage 10 have no diff/PR context on
  // the server, just what the triggering tab kept in sessionStorage.
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
  const context = {
    prTitle: data?.pr_title ?? metadata?.prTitle,
    repoFullName: data?.repo_full_name ?? metadata?.repoFullName,
    baseBranch: data?.base_branch ?? metadata?.baseBranch,
    prNumber: data?.pr_number ?? metadata?.prNumber,
    agent: data?.agent ?? metadata?.agent,
  };
  const diff = data?.diff ?? metadata?.diff ?? null;

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 p-6">
      <div className="flex flex-col gap-1">
        <Link href="/dashboard" className="text-sm text-muted-foreground underline underline-offset-4">
          &larr; Back to dashboard
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-lg font-semibold">
            {context.prTitle ?? `Run ${runId}`}
          </h1>
          <Badge variant={statusBadge.variant} className={statusBadge.className}>
            {status}
          </Badge>
        </div>
        {context.repoFullName && (
          <p className="text-sm text-muted-foreground">
            {context.repoFullName} &middot; {context.baseBranch} &middot; PR #{context.prNumber} &middot;{" "}
            {context.agent}
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
        {diff ? (
          <DiffViewer diffText={diff} findings={data?.findings ?? []} />
        ) : (
          <p className="text-sm text-muted-foreground">
            {status === "queued" || status === "running"
              ? "The diff appears once the worker has fetched it from GitHub."
              : "No diff is stored for this run (it predates server-side diff storage). Findings and their evidence trail still work below."}
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
