"use client";

import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { formatApiError, retryAnalysis, type AnalysisRunPublic } from "@/lib/api-client";
import { Button } from "@/components/ui/button";

/**
 * Run a failed review again. The retry is a new run (the failure stays in
 * the history), using the AI provider as configured now, so fixing the
 * provider and pressing this is the way out of a provider error. Opens the
 * new run once it is queued.
 */
export function RetryReview({ run }: { run: AnalysisRunPublic }) {
  const router = useRouter();
  const retry = useMutation({
    mutationFn: (anyway: boolean) => retryAnalysis(run.id, anyway ? { allow_over_ceiling: true } : {}),
    onSuccess: (created) => router.push(`/runs/${created.id}`),
  });
  const busy = retry.isPending || retry.isSuccess;

  if (run.status !== "failed") return null;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap gap-2">
        {run.over_ceiling ? (
          // Stopped at the repository's token limit before spending anything:
          // running it anyway is a deliberate choice to go over.
          <Button size="sm" disabled={busy} onClick={() => retry.mutate(true)}>
            <RotateCw />
            {busy ? "Starting…" : "Run anyway"}
          </Button>
        ) : (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => retry.mutate(false)}>
            <RotateCw />
            {busy ? "Starting…" : "Run review again"}
          </Button>
        )}
      </div>
      {retry.isError && (
        <p role="alert" className="text-xs text-destructive">
          {formatApiError(retry.error)}
        </p>
      )}
    </div>
  );
}
