"use client";

import { useState } from "react";
import type { Usage } from "@/lib/api-client";
import { formatDay, formatTokens, formatUsd, reviewsLabel } from "@/lib/usage";
import { cn } from "@/lib/utils";

type Metric = "cost" | "tokens";
type Day = Usage["daily"][number];

const CHART_HEIGHT = 128;

function value(day: Day, metric: Metric): number {
  return metric === "cost" ? day.cost_usd : day.tokens;
}

function describe(day: Day): string {
  return `${formatDay(day.day)}: ${formatUsd(day.cost_usd)} · ${day.tokens.toLocaleString("en-US")} tokens · ${reviewsLabel(day.runs)}`;
}

/**
 * This month, day by day: one bar per day, a single series, so no legend.
 * Pointing at (or tabbing to) a bar shows that day's figures above the
 * chart; the same figures are in the table below it. Local models report no
 * cost, so it starts on tokens when nothing has been spent.
 */
export function DailyChart({ daily }: { daily: Day[] }) {
  const spent = daily.some((d) => d.cost_usd > 0);
  const [metric, setMetric] = useState<Metric>(spent ? "cost" : "tokens");
  const [active, setActive] = useState<number | null>(null);
  const max = Math.max(...daily.map((d) => value(d, metric)), 0);
  const shown = active !== null ? daily[active] : null;
  const total = daily.reduce((sum, d) => sum + value(d, metric), 0);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="min-h-5 text-sm tabular-nums text-muted-foreground" aria-live="polite">
          {shown
            ? describe(shown)
            : `${metric === "cost" ? formatUsd(total) : `${formatTokens(total)} tokens`} this month`}
        </p>
        <div role="group" aria-label="Show" className="inline-flex rounded-md border p-0.5 text-xs">
          {(["cost", "tokens"] as const).map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={metric === m}
              onClick={() => setMetric(m)}
              className={cn(
                "rounded px-2 py-1 font-medium",
                metric === m ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {m === "cost" ? "Spend" : "Tokens"}
            </button>
          ))}
        </div>
      </div>

      <div
        className="flex items-end gap-0.5 border-b border-border"
        style={{ height: CHART_HEIGHT }}
        onMouseLeave={() => setActive(null)}
      >
        {daily.map((day, i) => {
          const v = value(day, metric);
          const height = max > 0 && v > 0 ? Math.max(2, (v / max) * CHART_HEIGHT) : 0;
          return (
            // The whole column is the hit target, not just the bar.
            <button
              key={day.day}
              type="button"
              aria-label={describe(day)}
              onMouseEnter={() => setActive(i)}
              onFocus={() => setActive(i)}
              onBlur={() => setActive(null)}
              className="group flex h-full min-w-0 flex-1 items-end focus-visible:outline-none"
            >
              <span
                className={cn(
                  "block w-full rounded-t-[4px] bg-primary/70 transition-colors group-hover:bg-primary group-focus-visible:bg-primary",
                  active === i && "bg-primary",
                )}
                style={{ height }}
              />
            </button>
          );
        })}
      </div>
      <div className="flex justify-between text-[11px] tabular-nums text-muted-foreground">
        <span>{daily.length > 0 ? formatDay(daily[0].day) : ""}</span>
        <span>{daily.length > 1 ? formatDay(daily[daily.length - 1].day) : ""}</span>
      </div>
    </div>
  );
}
