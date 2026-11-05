"use client";

import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { formatApiError, setBudget, type Budget } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { USAGE_QUERY_KEY } from "@/components/usage/budget-status";

/**
 * The organisation's monthly limits (owners and admins). An empty cap means
 * no cap of that kind. Saving replaces the whole budget.
 */
export function BudgetForm({ budget }: { budget: Budget }) {
  const queryClient = useQueryClient();
  const [usd, setUsd] = useState(budget.monthly_budget_usd?.toString() ?? "");
  const [tokens, setTokens] = useState(budget.token_cap?.toString() ?? "");
  const [warn, setWarn] = useState(String(budget.warn_percent));
  const [saved, setSaved] = useState(false);

  const save = useMutation({
    mutationFn: setBudget,
    onSuccess: () => {
      setSaved(true);
      void queryClient.invalidateQueries({ queryKey: USAGE_QUERY_KEY });
    },
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSaved(false);
    save.mutate({
      monthly_budget_usd: usd.trim() === "" ? null : usd.trim(),
      token_cap: tokens.trim() === "" ? null : Number(tokens),
      warn_percent: Number(warn),
    });
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="budget-usd">Monthly spend limit (USD)</Label>
          <Input
            id="budget-usd"
            type="number"
            inputMode="decimal"
            min={0}
            step="0.01"
            placeholder="No limit"
            value={usd}
            onChange={(e) => setUsd(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="budget-tokens">Monthly token limit</Label>
          <Input
            id="budget-tokens"
            type="number"
            inputMode="numeric"
            min={1}
            step={1}
            placeholder="No limit"
            value={tokens}
            onChange={(e) => setTokens(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="budget-warn">Warn from (% of a limit)</Label>
          <Input
            id="budget-warn"
            type="number"
            inputMode="numeric"
            min={1}
            max={100}
            required
            value={warn}
            onChange={(e) => setWarn(e.target.value)}
          />
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        Once a limit is reached, new reviews are refused until the 1st of next month (UTC). A review already running
        finishes. The token limit is the one to use for local models, which report no cost.
      </p>
      <div className="flex items-center gap-3">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save limits"}
        </Button>
        {saved && !save.isPending && <span className="text-sm text-muted-foreground">Saved.</span>}
        {save.isError && (
          <span role="alert" className="text-sm text-destructive">
            {formatApiError(save.error)}
          </span>
        )}
      </div>
    </form>
  );
}
