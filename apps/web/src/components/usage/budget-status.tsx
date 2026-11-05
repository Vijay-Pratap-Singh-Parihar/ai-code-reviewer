"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Ban, CheckCircle2 } from "lucide-react";
import { getUsage, type BudgetStatus, type Usage } from "@/lib/api-client";
import { formatDay, formatTokens, formatUsd } from "@/lib/usage";
import { cn } from "@/lib/utils";

export const USAGE_QUERY_KEY = ["usage"] as const;

export function useUsage() {
  return useQuery({ queryKey: USAGE_QUERY_KEY, queryFn: getUsage, staleTime: 30_000 });
}

// Status always pairs its color with an icon and a word, never color alone.
const STATUS: Record<BudgetStatus, { label: string; icon: typeof CheckCircle2; text: string; bar: string }> = {
  ok: {
    label: "Within budget",
    icon: CheckCircle2,
    text: "text-emerald-700 dark:text-emerald-400",
    bar: "bg-emerald-600 dark:bg-emerald-500",
  },
  warning: {
    label: "Nearing the limit",
    icon: AlertTriangle,
    text: "text-amber-700 dark:text-amber-400",
    bar: "bg-amber-500 dark:bg-amber-400",
  },
  exceeded: {
    label: "Limit reached",
    icon: Ban,
    text: "text-destructive",
    bar: "bg-destructive",
  },
};

export function BudgetStatusLabel({ status }: { status: BudgetStatus }) {
  const { label, icon: Icon, text } = STATUS[status];
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-sm font-medium", text)}>
      <Icon className="size-4" aria-hidden />
      {label}
    </span>
  );
}

/** How far through the month's cap the organisation is, one row per cap. */
export function BudgetMeter({ usage }: { usage: Usage }) {
  const { budget } = usage;
  const rows: { label: string; used: number; detail: string }[] = [];
  if (budget.monthly_budget_usd !== null) {
    rows.push({
      label: "Spend",
      used: budget.monthly_budget_usd > 0 ? (usage.spent_usd / budget.monthly_budget_usd) * 100 : 100,
      detail: `${formatUsd(usage.spent_usd)} of ${formatUsd(budget.monthly_budget_usd)}`,
    });
  }
  if (budget.token_cap !== null) {
    rows.push({
      label: "Tokens",
      used: (usage.tokens / budget.token_cap) * 100,
      detail: `${usage.tokens.toLocaleString("en-US")} of ${budget.token_cap.toLocaleString("en-US")}`,
    });
  }

  if (rows.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No monthly limit is set, so reviews are never stopped for cost.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {rows.map((row) => {
        const state: BudgetStatus = row.used >= 100 ? "exceeded" : row.used >= budget.warn_percent ? "warning" : "ok";
        return (
          <div key={row.label} className="flex flex-col gap-1.5">
            <div className="flex items-baseline justify-between gap-2 text-sm">
              <span className="font-medium">{row.label}</span>
              <span className="tabular-nums text-muted-foreground">
                {row.detail} · {Math.round(row.used)}%
              </span>
            </div>
            <div
              role="meter"
              aria-label={`${row.label} used this month`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.min(100, Math.round(row.used))}
              aria-valuetext={`${row.detail}, ${Math.round(row.used)}%`}
              className="relative h-2 overflow-hidden rounded-full bg-muted"
            >
              <div
                className={cn("h-full rounded-full transition-[width]", STATUS[state].bar)}
                style={{ width: `${Math.min(100, row.used)}%` }}
              />
              {/* Where the warning starts. */}
              <div
                aria-hidden
                className="absolute inset-y-0 w-0.5 bg-background"
                style={{ left: `${budget.warn_percent}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * A line above a page's content once the organisation nears or reaches its
 * monthly limit; nothing otherwise.
 */
export function BudgetBanner() {
  const { data } = useUsage();
  if (!data || data.status === "ok") return null;
  const exceeded = data.status === "exceeded";
  const spend =
    data.budget.monthly_budget_usd !== null
      ? `${formatUsd(data.spent_usd)} of ${formatUsd(data.budget.monthly_budget_usd)}`
      : `${formatTokens(data.tokens)} of ${formatTokens(data.budget.token_cap ?? 0)} tokens`;
  return (
    <div
      role={exceeded ? "alert" : "status"}
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-4 py-3 text-sm",
        exceeded ? "border-destructive/40 bg-destructive/5" : "border-amber-500/40 bg-amber-500/5",
      )}
    >
      <BudgetStatusLabel status={data.status} />
      <span className="text-muted-foreground">
        {exceeded
          ? `New reviews are paused until ${formatDay(data.resets_at)} (${spend} used this month).`
          : `${spend} used this month.`}
      </span>
      <Link href="/usage" className="font-medium underline underline-offset-4">
        Usage & Budget
      </Link>
    </div>
  );
}
