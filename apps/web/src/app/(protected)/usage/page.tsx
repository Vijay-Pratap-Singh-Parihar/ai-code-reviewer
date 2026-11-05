"use client";

import { UsageOverview } from "@/components/usage/usage-overview";

export default function UsagePage() {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-lg font-semibold">Usage & Budget</h1>
        <p className="text-sm text-muted-foreground">
          What reviews have cost your organization this month, where it went, and the monthly limits that stop new
          reviews once they are reached. Figures count finished reviews.
        </p>
      </div>
      <UsageOverview />
    </div>
  );
}
