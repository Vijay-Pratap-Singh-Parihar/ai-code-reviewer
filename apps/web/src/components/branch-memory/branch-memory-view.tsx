"use client";

import { Fragment } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, CircleDashed, Loader2 } from "lucide-react";
import { formatApiError, getBranchMemory, indexRepository, type BranchMemoryRow } from "@/lib/api-client";
import { formatDateTime } from "@/lib/lifecycle";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const POLL_INTERVAL_MS = 3000;
export const BRANCH_MEMORY_QUERY_KEY = ["branch-memory"] as const;

// State is always an icon and a word, never color alone.
const STATE: Record<BranchMemoryRow["state"], { label: string; icon: typeof CheckCircle2; className: string }> = {
  ready: { label: "Ready", icon: CheckCircle2, className: "text-emerald-700 dark:text-emerald-400" },
  building: { label: "Building", icon: Loader2, className: "text-foreground" },
  failed: { label: "Last build failed", icon: AlertTriangle, className: "text-destructive" },
  not_indexed: { label: "Not indexed", icon: CircleDashed, className: "text-muted-foreground" },
};

function StateLabel({ row }: { row: BranchMemoryRow }) {
  const { label, icon: Icon, className } = STATE[row.state];
  return (
    <div className="flex flex-col gap-0.5">
      <span className={cn("inline-flex items-center gap-1.5 text-sm font-medium", className)}>
        <Icon className={cn("size-4", row.state === "building" && "animate-spin")} aria-hidden />
        {label}
      </span>
      {row.state !== "ready" && row.has_ready_index && (
        <span className="text-xs text-muted-foreground">Reviews use the previous index</span>
      )}
    </div>
  );
}

function seconds(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "–";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function percent(rate: number | null, capped: boolean): string {
  if (rate === null) return "–";
  return `${capped ? "≥ " : ""}${(rate * 100).toFixed(rate < 0.1 ? 1 : 0)}%`;
}

function RebuildButton({ row }: { row: BranchMemoryRow }) {
  const queryClient = useQueryClient();
  const build = useMutation({
    mutationFn: () => indexRepository(row.repo_id, { branch_name: row.branch_name, force_full: true }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: BRANCH_MEMORY_QUERY_KEY });
      void queryClient.invalidateQueries({ queryKey: ["branch-index", row.repo_id] });
    },
  });
  if (!row.repo_active) return <span className="text-xs text-muted-foreground">Disconnected</span>;
  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        size="sm"
        variant="outline"
        disabled={build.isPending || row.state === "building"}
        onClick={() => build.mutate()}
        aria-label={`${row.has_ready_index ? "Rebuild" : "Build"} the index of ${row.repo_full_name} ${row.branch_name}`}
      >
        {row.has_ready_index ? "Rebuild" : "Build"}
      </Button>
      {build.isError && (
        <span role="alert" className="max-w-48 text-right text-xs text-destructive">
          {formatApiError(build.error)}
        </span>
      )}
    </div>
  );
}

/**
 * Branch Memory: the prebuilt code graph each branch's cross-file reviews
 * read. Per branch: whether it's usable, its size, how many references the
 * indexer couldn't resolve, how it was last updated and how long that took,
 * and how often it has had to be rebuilt from scratch lately (the
 * operational cost of force-pushes and rewritten history).
 */
export function BranchMemoryView() {
  const memory = useQuery({
    queryKey: BRANCH_MEMORY_QUERY_KEY,
    queryFn: getBranchMemory,
    refetchInterval: (query) =>
      query.state.data?.some((row) => row.state === "building") ? POLL_INTERVAL_MS : false,
  });

  if (memory.isPending) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (memory.isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {formatApiError(memory.error)}
      </p>
    );
  }
  const rows = memory.data;
  if (rows.length === 0) {
    return (
      <Card>
        <CardContent className="text-sm text-muted-foreground">
          No GitHub repositories are connected yet. Connect one on the{" "}
          <Link href="/github" className="font-medium text-foreground underline underline-offset-4">
            GitHub
          </Link>{" "}
          page, then build its index here.
        </CardContent>
      </Card>
    );
  }

  const ready = rows.filter((r) => r.has_ready_index).length;
  const rebuilds = rows.reduce((sum, r) => sum + r.full_rebuilds_30d, 0);
  const incremental = rows.reduce((sum, r) => sum + r.incremental_updates_30d, 0);
  const repos = [...new Set(rows.map((r) => r.repo_full_name))];

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Card>
          <CardContent className="flex flex-col gap-1">
            <span className="text-2xl font-semibold tabular-nums">
              {ready} / {rows.length}
            </span>
            <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Branches with a usable index
            </span>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex flex-col gap-1">
            <span className="text-2xl font-semibold tabular-nums">{incremental}</span>
            <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Incremental updates, 30 days
            </span>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex flex-col gap-1">
            <span className="text-2xl font-semibold tabular-nums">{rebuilds}</span>
            <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Full rebuilds, 30 days
            </span>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Branch</TableHead>
                <TableHead>State</TableHead>
                <TableHead className="text-right">Nodes</TableHead>
                <TableHead className="text-right">Edges</TableHead>
                <TableHead className="text-right">Unresolved</TableHead>
                <TableHead>Last update</TableHead>
                <TableHead className="text-right">Rebuilds (30 d)</TableHead>
                <TableHead className="text-right">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {repos.map((repo) => (
                <Fragment key={repo}>
                  <TableRow className="hover:bg-transparent">
                    <TableCell colSpan={8} className="bg-muted/40 py-1.5 text-xs font-semibold">
                      {repo}
                    </TableCell>
                  </TableRow>
                  {rows
                    .filter((r) => r.repo_full_name === repo)
                    .map((row) => (
                      <TableRow key={`${row.repo_id}:${row.branch_name}`}>
                        <TableCell>
                          <span className="font-mono text-sm">{row.branch_name}</span>
                          {row.is_default_branch && (
                            <span className="ml-2 text-xs text-muted-foreground">default</span>
                          )}
                          {row.head_sha && (
                            <div className="font-mono text-xs text-muted-foreground">{row.head_sha.slice(0, 7)}</div>
                          )}
                        </TableCell>
                        <TableCell>
                          <StateLabel row={row} />
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {row.has_ready_index ? row.node_count.toLocaleString("en-US") : "–"}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {row.has_ready_index ? row.edge_count.toLocaleString("en-US") : "–"}
                        </TableCell>
                        <TableCell
                          className="text-right tabular-nums"
                          title={
                            row.has_ready_index
                              ? `${row.unresolved_capped ? "At least " : ""}${row.unresolved_count} references the indexer couldn't resolve`
                              : undefined
                          }
                        >
                          {percent(row.unresolved_rate, row.unresolved_capped)}
                        </TableCell>
                        <TableCell className="text-sm">
                          {row.last_update ? (
                            <>
                              <span className="capitalize">{row.last_update.mode}</span>
                              <span className="text-muted-foreground"> · {seconds(row.last_update.duration_ms)}</span>
                              <div className="text-xs text-muted-foreground">
                                {formatDateTime(row.last_update.created_at)}
                              </div>
                              {row.state === "failed" && row.last_update.reason && (
                                <div className="max-w-64 truncate text-xs text-destructive" title={row.last_update.reason}>
                                  {row.last_update.reason}
                                </div>
                              )}
                            </>
                          ) : (
                            <span className="text-muted-foreground">–</span>
                          )}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {row.full_rebuilds_30d}
                          <span className="text-xs text-muted-foreground"> / {row.incremental_updates_30d} incr.</span>
                        </TableCell>
                        <TableCell className="text-right">
                          <RebuildButton row={row} />
                        </TableCell>
                      </TableRow>
                    ))}
                </Fragment>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
