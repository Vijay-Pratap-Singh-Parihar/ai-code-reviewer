import type { AnalysisRunStatus } from "@/lib/api-client";

export type BadgeVariant = "default" | "secondary" | "destructive" | "outline";
export type BadgeStyle = { variant: BadgeVariant; className?: string };

export const STATUS_BADGE: Record<AnalysisRunStatus, BadgeStyle> = {
  queued: { variant: "secondary" },
  running: { variant: "default" },
  succeeded: {
    variant: "outline",
    className: "border-emerald-600/40 text-emerald-700 dark:text-emerald-400",
  },
  failed: { variant: "destructive" },
};

export const SEVERITY_BADGE: Record<string, BadgeStyle> = {
  low: { variant: "secondary" },
  medium: { variant: "outline" },
  high: {
    variant: "outline",
    className: "border-amber-600/40 text-amber-700 dark:text-amber-400",
  },
  critical: { variant: "destructive" },
};
