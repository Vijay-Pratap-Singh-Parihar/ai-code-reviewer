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
    mutationFn: () => retryAnalysis(run.id),
    onSuccess: (created) => router.push(`/runs/${created.id}`),
  });

  if (run.status !== "failed") return null;

  return (
    <div className="flex flex-col gap-1">
      <div>
        <Button size="sm" variant="outline" disabled={retry.isPending || retry.isSuccess} onClick={() => retry.mutate()}>
          <RotateCw />
          {retry.isPending || retry.isSuccess ? "Starting…" : "Run review again"}
        </Button>
      </div>
      {retry.isError && (
        <p role="alert" className="text-xs text-destructive">
          {formatApiError(retry.error)}
        </p>
      )}
    </div>
  );
}
