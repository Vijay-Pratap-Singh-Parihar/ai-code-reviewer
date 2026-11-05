"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { getAdoptionStats, getBranchMemory, listHistory } from "@/lib/api-client";
import { STATUS_BADGE } from "@/lib/badges";
import { formatDateTime } from "@/lib/lifecycle";
import { formatUsd } from "@/lib/usage";
import { useUsage } from "@/components/usage/budget-status";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function Tile({ label, value, hint, href }: { label: string; value: string; hint?: string; href: string }) {
  return (
    <Link href={href} className="rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-ring">
      <Card className="h-full transition-colors hover:bg-muted/40">
        <CardContent className="flex flex-col gap-1">
          <span className="text-2xl font-semibold tabular-nums">{value}</span>
          <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</span>
          {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
        </CardContent>
      </Card>
    </Link>
  );
}

/**
 * The dashboard's summary (Architecture §6): this month's spend against the
 * budget, how reviews have been received, index health across repositories,
 * and the latest reviews. Each tile opens the screen it summarises.
 */
export function DashboardOverview() {
  const usage = useUsage();
  const stats = useQuery({ queryKey: ["history-stats", null], queryFn: () => getAdoptionStats(null) });
  const memory = useQuery({ queryKey: ["branch-memory"], queryFn: getBranchMemory });
  const recent = useQuery({ queryKey: ["history", "recent"], queryFn: () => listHistory({ limit: 5 }) });

  const u = usage.data;
  const budget = u?.budget.monthly_budget_usd;
  const s = stats.data;
  const branches = memory.data ?? [];
  const ready = branches.filter((b) => b.has_ready_index).length;
  const failing = branches.filter((b) => b.state === "failed").length;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile
          href="/usage"
          label="Spent this month"
          value={u ? formatUsd(u.spent_usd) : "–"}
          hint={
            u
              ? budget !== null && budget !== undefined
                ? `of ${formatUsd(budget)} budget${u.used_percent !== null ? ` · ${Math.round(u.used_percent)}%` : ""}`
                : "No monthly limit set"
              : undefined
          }
        />
        <Tile
          href="/history"
          label="Reviews"
          value={s ? s.runs.toLocaleString("en-US") : "–"}
          hint={s ? `${s.succeeded} succeeded · ${s.failed} failed` : undefined}
        />
        <Tile
          href="/history"
          label="Findings accepted"
          value={s?.acceptance_rate != null ? `${Math.round(s.acceptance_rate * 100)}%` : "–"}
          hint={s ? (s.accepted + s.dismissed > 0 ? `${s.accepted} of ${s.accepted + s.dismissed} with feedback` : "No feedback yet") : undefined}
        />
        <Tile
          href="/branch-memory"
          label="Branches indexed"
          value={memory.data ? `${ready} / ${branches.length}` : "–"}
          hint={failing > 0 ? `${failing} last build failed` : memory.data ? "All builds healthy" : undefined}
        />
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle>Recent reviews</CardTitle>
            <Link href="/history" className="text-sm text-muted-foreground underline underline-offset-4">
              All history
            </Link>
          </div>
        </CardHeader>
        <CardContent>
          {recent.data && recent.data.runs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No reviews yet. Review a pull request from a repository below.</p>
          ) : (
            <ul className="flex flex-col divide-y">
              {(recent.data?.runs ?? []).map((run) => (
                <li key={run.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                  <Link href={`/runs/${run.id}`} className="min-w-0 flex-1 hover:underline">
                    <span className="block truncate font-medium">{run.pr_title}</span>
                    <span className="text-xs text-muted-foreground">
                      {run.repo_full_name}#{run.pr_number} · {formatDateTime(run.created_at)}
                    </span>
                  </Link>
                  <span className="text-xs text-muted-foreground">
                    {run.findings} {run.findings === 1 ? "finding" : "findings"}
                  </span>
                  <Badge variant={STATUS_BADGE[run.status].variant} className={STATUS_BADGE[run.status].className}>
                    {run.status}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
