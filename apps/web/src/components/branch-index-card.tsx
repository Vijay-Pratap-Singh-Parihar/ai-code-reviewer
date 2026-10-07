"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Network } from "lucide-react";
import {
  formatApiError,
  getBranchIndexStatus,
  indexRepository,
  type RepositoryPublic,
} from "@/lib/api-client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const POLL_INTERVAL_MS = 2000;
const IN_FLIGHT = new Set(["pending", "building"]);

export function useBranchIndexStatus(repo: RepositoryPublic) {
  return useQuery({
    queryKey: ["branch-index", repo.id, repo.default_branch],
    queryFn: () => getBranchIndexStatus(repo.id, repo.default_branch),
    refetchInterval: (query) =>
      IN_FLIGHT.has(query.state.data?.latest_attempt_status ?? "") ? POLL_INTERVAL_MS : false,
  });
}

/**
 * Branch memory for the repo's default branch: what `cross_file` reviews
 * read. Building is free (no LLM) — the worker fetches the branch from
 * GitHub and indexes it — and later pushes keep it fresh automatically.
 */
export function BranchIndexCard({ repo }: { repo: RepositoryPublic }) {
  const queryClient = useQueryClient();
  const status = useBranchIndexStatus(repo);
  const build = useMutation({
    mutationFn: () => indexRepository(repo.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["branch-index", repo.id] });
    },
  });

  const data = status.data;
  const inFlight = IN_FLIGHT.has(data?.latest_attempt_status ?? "");
  const lastFailed = data?.latest_attempt_status === "failed";

  return (
    <div className="flex flex-col gap-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Network className="size-4 text-muted-foreground" />
        <span>
          Index of <span className="font-mono">{repo.default_branch}</span>
        </span>
        {status.isPending ? null : data?.has_ready_index ? (
          <Badge variant="outline" className="border-emerald-600/40 text-emerald-700 dark:text-emerald-400">
            ready
          </Badge>
        ) : (
          <Badge variant="secondary">not built</Badge>
        )}
        {inFlight && <Badge>{data?.latest_attempt_status}</Badge>}
        {lastFailed && <Badge variant="destructive">last build failed</Badge>}
      </div>

      {data?.has_ready_index && (
        <p className="text-xs text-muted-foreground">
          {data.node_count} nodes · {data.edge_count} edges · at{" "}
          <span className="font-mono">{data.head_sha?.slice(0, 7)}</span>
          {data.built_at && ` · built ${new Date(data.built_at).toLocaleString()}`}
        </p>
      )}
      {status.isError && (
        <p role="alert" className="text-xs text-destructive">
          {formatApiError(status.error)}
        </p>
      )}
      {build.isError && (
        <p role="alert" className="text-xs text-destructive">
          {formatApiError(build.error)}
        </p>
      )}

      <div>
        <Button
          variant="outline"
          size="sm"
          disabled={build.isPending || inFlight}
          onClick={() => build.mutate()}
        >
          {inFlight ? "Building…" : data?.has_ready_index ? "Rebuild index" : "Build index"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Needed only for <code className="font-mono">cross_file</code> reviews. Free, no LLM calls.
      </p>
    </div>
  );
}
