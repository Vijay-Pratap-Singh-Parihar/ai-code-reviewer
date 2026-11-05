"use client";

import { useState } from "react";
import Link from "next/link";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import {
  formatApiError,
  getAdoptionStats,
  listHistory,
  listRepositories,
  type AnalysisRunStatus,
  type HistoryFilters,
  type HistoryRun,
  type PullRequestState,
  type Severity,
} from "@/lib/api-client";
import { SEVERITY_BADGE, STATUS_BADGE } from "@/lib/badges";
import { formatDateTime } from "@/lib/lifecycle";
import { formatTokens, formatUsd } from "@/lib/usage";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const PAGE_SIZE = 50;
const ALL = "all";
const SEVERITIES: Severity[] = ["critical", "high", "medium", "low"];

const SEVERITY_FILTER: Record<string, string> = {
  [ALL]: "Any findings",
  critical: "Critical",
  high: "High or above",
  medium: "Medium or above",
  low: "Any finding",
};
const OUTCOME_FILTER: Record<string, string> = {
  [ALL]: "Any outcome",
  succeeded: "Succeeded",
  failed: "Failed",
  running: "Running",
  queued: "Queued",
};
const PR_STATE_FILTER: Record<string, string> = {
  [ALL]: "Any pull request",
  open: "Open",
  merged: "Merged",
  closed: "Closed without merging",
};

function FilterSelect({
  id,
  label,
  value,
  options,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  options: Record<string, string>;
  onChange: (value: string) => void;
}) {
  return (
    <div className="flex min-w-44 flex-1 flex-col gap-1.5">
      <span id={id} className="text-sm font-medium">
        {label}
      </span>
      <Select value={value} onValueChange={(v) => onChange(String(v ?? ALL))}>
        <SelectTrigger aria-labelledby={id} className="w-full">
          <SelectValue>{(v: string) => options[v] ?? v}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          {Object.entries(options).map(([key, text]) => (
            <SelectItem key={key} value={key}>
              {text}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function StatTile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-1">
        <span className="text-2xl font-semibold tabular-nums">{value}</span>
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</span>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </CardContent>
    </Card>
  );
}

function AdoptionTiles({ repoId }: { repoId: string | null }) {
  const stats = useQuery({ queryKey: ["history-stats", repoId], queryFn: () => getAdoptionStats(repoId) });
  const s = stats.data;
  if (!s) return null;
  const judged = s.accepted + s.dismissed;
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
      <StatTile
        label="Reviews"
        value={s.runs.toLocaleString("en-US")}
        hint={`${s.succeeded} succeeded · ${s.failed} failed`}
      />
      <StatTile
        label="Pull requests"
        value={s.pull_requests.toLocaleString("en-US")}
        hint={`${s.merged_pull_requests} merged`}
      />
      <StatTile
        label="Findings"
        value={s.findings.toLocaleString("en-US")}
        hint={SEVERITIES.filter((sev) => s.by_severity[sev])
          .map((sev) => `${s.by_severity[sev]} ${sev}`)
          .join(" · ")}
      />
      <StatTile
        label="Accepted"
        value={s.acceptance_rate === null ? "–" : `${Math.round(s.acceptance_rate * 100)}%`}
        hint={judged ? `${s.accepted} of ${judged} findings with feedback` : "No feedback on findings yet"}
      />
      <StatTile label="Posted to GitHub" value={s.posted_to_github.toLocaleString("en-US")} hint="reviews" />
    </div>
  );
}

function SeverityBadges({ run }: { run: HistoryRun }) {
  if (run.findings === 0) return <span className="text-muted-foreground">None</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {SEVERITIES.filter((sev) => run.severities[sev]).map((sev) => (
        <Badge key={sev} variant={SEVERITY_BADGE[sev].variant} className={SEVERITY_BADGE[sev].className}>
          {run.severities[sev]} {sev}
        </Badge>
      ))}
    </div>
  );
}

function trigger(run: HistoryRun): string {
  if (run.source === "manual") return run.trigger === "retry" ? "Pasted diff (retry)" : "Pasted diff";
  if (run.trigger === "webhook") return "Automatic";
  if (run.trigger === "retry") return "Retry";
  return "Requested";
}

/**
 * History: every review the organisation has run, newest first, filtered
 * by repository, severity, outcome and pull request state, with adoption
 * statistics for the chosen repository.
 */
export function HistoryView() {
  const [repo, setRepo] = useState(ALL);
  const [severity, setSeverity] = useState(ALL);
  const [outcome, setOutcome] = useState(ALL);
  const [prState, setPrState] = useState(ALL);

  const repos = useQuery({ queryKey: ["repositories"], queryFn: listRepositories });
  const filters: HistoryFilters = {
    repo_id: repo === ALL ? null : repo,
    severity: severity === ALL ? null : (severity as Severity),
    outcome: outcome === ALL ? null : (outcome as AnalysisRunStatus),
    pr_state: prState === ALL ? null : (prState as PullRequestState),
  };
  const history = useInfiniteQuery({
    queryKey: ["history", filters],
    queryFn: ({ pageParam }) => listHistory({ ...filters, before: pageParam, limit: PAGE_SIZE }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_before,
  });
  const runs = history.data?.pages.flatMap((page) => page.runs) ?? [];

  const repoOptions: Record<string, string> = { [ALL]: "All repositories" };
  for (const r of repos.data ?? []) repoOptions[r.id] = r.full_name;

  return (
    <div className="flex flex-col gap-6">
      <AdoptionTiles repoId={filters.repo_id ?? null} />

      <Card>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-wrap gap-3">
            <FilterSelect id="history-repo" label="Repository" value={repo} options={repoOptions} onChange={setRepo} />
            <FilterSelect
              id="history-severity"
              label="Findings"
              value={severity}
              options={SEVERITY_FILTER}
              onChange={setSeverity}
            />
            <FilterSelect
              id="history-outcome"
              label="Outcome"
              value={outcome}
              options={OUTCOME_FILTER}
              onChange={setOutcome}
            />
            <FilterSelect
              id="history-pr-state"
              label="Pull request"
              value={prState}
              options={PR_STATE_FILTER}
              onChange={setPrState}
            />
          </div>

          {history.isPending ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : history.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {formatApiError(history.error)}
            </p>
          ) : runs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No reviews match these filters.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When</TableHead>
                  <TableHead>Pull request</TableHead>
                  <TableHead>Outcome</TableHead>
                  <TableHead>Findings</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead className="text-right">Tokens</TableHead>
                  <TableHead className="text-right">Spend</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell className="whitespace-nowrap text-muted-foreground">
                      <Link href={`/runs/${run.id}`} className="hover:underline">
                        {formatDateTime(run.created_at)}
                      </Link>
                      <div className="text-xs">{trigger(run)}</div>
                    </TableCell>
                    <TableCell className="max-w-72">
                      <Link href={`/runs/${run.id}`} className="font-medium hover:underline">
                        <span className="block truncate">{run.pr_title}</span>
                      </Link>
                      <span className="text-xs text-muted-foreground">
                        {run.repo_full_name}#{run.pr_number} · {run.pr_state}
                      </span>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col items-start gap-1">
                        <Badge
                          variant={STATUS_BADGE[run.status].variant}
                          className={STATUS_BADGE[run.status].className}
                        >
                          {run.status}
                        </Badge>
                        {run.github_review_url && (
                          <a
                            href={run.github_review_url}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:underline"
                          >
                            On GitHub <ExternalLink className="size-3" aria-hidden />
                          </a>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <SeverityBadges run={run} />
                    </TableCell>
                    <TableCell className="max-w-40 truncate text-muted-foreground">{run.model ?? "–"}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatTokens(run.tokens)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatUsd(run.cost_usd)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          {history.hasNextPage && (
            <Button
              variant="outline"
              className="self-start"
              disabled={history.isFetchingNextPage}
              onClick={() => void history.fetchNextPage()}
            >
              {history.isFetchingNextPage ? "Loading…" : "Load older reviews"}
            </Button>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
