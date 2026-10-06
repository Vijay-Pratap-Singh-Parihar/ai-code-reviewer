"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { formatApiError, updateRepository, type RepositoryPublic } from "@/lib/api-client";
import { Switch } from "@/components/ui/switch";

/**
 * Per-repository opt-in for reviewing PRs automatically when they're opened
 * or pushed to. Off by default (server-side default too): every review is a
 * billed LLM call, so nothing is spent until a person turns this on.
 */
export function AutoReviewToggle({ repo, showHint = false }: { repo: RepositoryPublic; showHint?: boolean }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (enabled: boolean) => updateRepository(repo.id, { auto_review_enabled: enabled }),
    onSuccess: (updated) => {
      queryClient.setQueryData<RepositoryPublic[]>(["repositories"], (repos) =>
        repos?.map((r) => (r.id === updated.id ? updated : r)),
      );
    },
  });

  const checked = mutation.isPending ? Boolean(mutation.variables) : repo.auto_review_enabled;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Switch
          aria-label={`Auto-review ${repo.full_name}`}
          checked={checked}
          disabled={mutation.isPending || !repo.connected}
          onCheckedChange={(value) => mutation.mutate(value)}
        />
        <span className="text-sm">Auto-review</span>
      </div>
      {showHint && (
        <p className="text-xs text-muted-foreground">
          {checked
            ? "New and updated PRs are reviewed automatically (diff_only, billed). Drafts are skipped."
            : "Off — PRs are only reviewed when you click Review."}
        </p>
      )}
      {mutation.isError && (
        <p role="alert" className="text-xs text-destructive">
          {formatApiError(mutation.error)}
        </p>
      )}
    </div>
  );
}
