"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, ThumbsDown, ThumbsUp, Undo2 } from "lucide-react";
import {
  formatApiError,
  giveFindingFeedback,
  type AnalysisRunPublic,
  type DismissReason,
  type FindingPublic,
} from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export const DISMISS_REASONS: Record<DismissReason, string> = {
  incorrect: "Incorrect",
  not_important: "Not important",
  already_handled: "Already handled",
  other: "Other",
};

/**
 * "Was this finding useful?" for one finding on the run page. Accepting
 * or dismissing takes one click; after a dismissal the reason can be
 * picked (a wrong finding and a right-but-unimportant one are different
 * failures). Feedback feeds the acceptance rate on the History page.
 */
export function FindingFeedback({ runId, finding }: { runId: string; finding: FindingPublic }) {
  const queryClient = useQueryClient();
  const send = useMutation({
    mutationFn: (body: { action: "accepted" | "dismissed" | null; reason?: DismissReason | null }) =>
      giveFindingFeedback(runId, finding.id, body),
    onSuccess: (updated) => {
      queryClient.setQueryData<AnalysisRunPublic>(["analysis-run", runId], (run) =>
        run ? { ...run, findings: run.findings.map((f) => (f.id === updated.id ? updated : f)) } : run,
      );
      void queryClient.invalidateQueries({ queryKey: ["history-stats"] });
    },
  });

  const action = finding.developer_action ?? null;
  const pending = send.isPending;

  return (
    <div className="flex flex-col gap-2 border-t pt-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {action === null ? (
          <>
            <span className="text-muted-foreground">Was this useful?</span>
            <Button
              size="sm"
              variant="outline"
              disabled={pending}
              onClick={() => send.mutate({ action: "accepted" })}
            >
              <ThumbsUp className="size-3.5" aria-hidden />
              Accept
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={pending}
              onClick={() => send.mutate({ action: "dismissed" })}
            >
              <ThumbsDown className="size-3.5" aria-hidden />
              Dismiss
            </Button>
          </>
        ) : (
          <>
            <span
              className={cn(
                "inline-flex items-center gap-1 font-medium",
                action === "accepted" ? "text-emerald-700 dark:text-emerald-400" : "text-muted-foreground",
              )}
            >
              {action === "accepted" ? (
                <ThumbsUp className="size-3.5" aria-hidden />
              ) : (
                <ThumbsDown className="size-3.5" aria-hidden />
              )}
              {action === "accepted" ? "Accepted" : "Dismissed"}
              {action === "dismissed" && finding.feedback_reason && ` · ${DISMISS_REASONS[finding.feedback_reason]}`}
            </span>
            <Button size="sm" variant="ghost" disabled={pending} onClick={() => send.mutate({ action: null })}>
              <Undo2 className="size-3.5" aria-hidden />
              Undo
            </Button>
          </>
        )}
      </div>

      {action === "dismissed" && (
        <div role="group" aria-label="Why was it dismissed?" className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-muted-foreground">Why?</span>
          {(Object.keys(DISMISS_REASONS) as DismissReason[]).map((reason) => {
            const chosen = finding.feedback_reason === reason;
            return (
              <button
                key={reason}
                type="button"
                aria-pressed={chosen}
                disabled={pending}
                onClick={() => send.mutate({ action: "dismissed", reason })}
                className={cn(
                  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5",
                  chosen ? "border-foreground/40 bg-muted font-medium" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {chosen && <Check className="size-3" aria-hidden />}
                {DISMISS_REASONS[reason]}
              </button>
            );
          })}
        </div>
      )}

      {send.isError && (
        <p role="alert" className="text-xs text-destructive">
          {formatApiError(send.error)}
        </p>
      )}
    </div>
  );
}
