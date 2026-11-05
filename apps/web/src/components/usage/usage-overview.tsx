"use client";

import type { ReactNode } from "react";
import { useAuth } from "@/components/auth-provider";
import { formatApiError, type UsageTotals } from "@/lib/api-client";
import { isOrgAdmin } from "@/lib/permissions";
import { formatDay, formatMonth, formatTokens, formatUsd } from "@/lib/usage";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { BudgetForm } from "@/components/usage/budget-form";
import { BudgetMeter, BudgetStatusLabel, useUsage } from "@/components/usage/budget-status";
import { DailyChart } from "@/components/usage/daily-chart";

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

function TotalsTable<T extends UsageTotals>({
  rows,
  name,
  nameHeader,
  empty,
}: {
  rows: T[];
  name: (row: T) => ReactNode;
  nameHeader: string;
  empty: string;
}) {
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">{empty}</p>;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{nameHeader}</TableHead>
          <TableHead className="text-right">Reviews</TableHead>
          <TableHead className="text-right">Tokens</TableHead>
          <TableHead className="text-right">Spend</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, i) => (
          <TableRow key={i}>
            <TableCell className="max-w-64 truncate">{name(row)}</TableCell>
            <TableCell className="text-right tabular-nums">{row.runs.toLocaleString("en-US")}</TableCell>
            <TableCell className="text-right tabular-nums">{formatTokens(row.tokens)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatUsd(row.cost_usd)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/**
 * Usage & Budget: this month against the organisation's limits, where the
 * spend went (day, repository, model), the months before, and, for owners
 * and admins, the limits themselves.
 */
export function UsageOverview() {
  const { user } = useAuth();
  const { data: usage, isPending, isError, error } = useUsage();

  if (isPending) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (isError) return <p className="text-sm text-destructive">{formatApiError(error)}</p>;

  const resets = formatDay(usage.resets_at);

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Spent this month" value={formatUsd(usage.spent_usd)} />
        <StatTile label="Tokens this month" value={formatTokens(usage.tokens)} />
        <StatTile label="Reviews this month" value={usage.runs.toLocaleString("en-US")} />
        <StatTile label="Resets" value={resets} hint="1st of the month, UTC" />
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>Monthly limit</CardTitle>
            {usage.used_percent !== null && <BudgetStatusLabel status={usage.status} />}
          </div>
          {usage.status === "exceeded" && (
            <CardDescription>New reviews are refused until {resets} unless a limit is raised.</CardDescription>
          )}
        </CardHeader>
        <CardContent className="flex flex-col gap-6">
          <BudgetMeter usage={usage} />
          {isOrgAdmin(user) ? (
            <BudgetForm key={JSON.stringify(usage.budget)} budget={usage.budget} />
          ) : (
            <p className="text-xs text-muted-foreground">Organization owners and admins can change the limits.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>This month, day by day</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <DailyChart daily={usage.daily} />
          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground">Show as a table</summary>
            <div className="pt-2">
              <TotalsTable
                rows={usage.daily.filter((d) => d.runs > 0)}
                name={(d) => formatDay(d.day)}
                nameHeader="Day"
                empty="No reviews finished this month yet."
              />
            </div>
          </details>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>By repository</CardTitle>
          </CardHeader>
          <CardContent>
            <TotalsTable
              rows={usage.by_repo}
              name={(r) => r.repo_full_name}
              nameHeader="Repository"
              empty="No reviews finished this month yet."
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>By model</CardTitle>
          </CardHeader>
          <CardContent>
            <TotalsTable
              rows={usage.by_model}
              name={(m) => (
                <span>
                  {m.model} <span className="text-muted-foreground">· {m.provider_name}</span>
                </span>
              )}
              nameHeader="Model"
              empty="No reviews finished this month yet."
            />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Recent months</CardTitle>
        </CardHeader>
        <CardContent>
          <TotalsTable
            rows={[...usage.months].reverse()}
            name={(m) => formatMonth(m.month)}
            nameHeader="Month"
            empty="No usage yet."
          />
        </CardContent>
      </Card>
    </div>
  );
}
