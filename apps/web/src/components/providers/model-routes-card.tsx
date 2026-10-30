"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  formatApiError,
  setModelRoutes,
  type ModelRoutePublic,
  type ModelTier,
  type ProviderPublic,
  type RouteUpdate,
} from "@/lib/api-client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const NONE = "none";

export const TIERS: { tier: ModelTier; label: string; description: string; inUse: boolean }[] = [
  {
    tier: "review",
    label: "Review",
    description: "Writes every review, diff-only and cross-file. Reviews are refused until this is set.",
    inUse: true,
  },
  {
    tier: "screen",
    label: "Screen",
    description: "A cheaper model to triage pull requests before a full review.",
    inUse: false,
  },
  {
    tier: "verify",
    label: "Verify",
    description: "A second model to double-check findings.",
    inUse: false,
  },
];

type Draft = Record<ModelTier, { providerId: string; model: string }>;

function draftFrom(routes: ModelRoutePublic[]): Draft {
  const draft: Draft = {
    review: { providerId: NONE, model: "" },
    screen: { providerId: NONE, model: "" },
    verify: { providerId: NONE, model: "" },
  };
  for (const route of routes) draft[route.tier] = { providerId: route.provider_id, model: route.model };
  return draft;
}

/** Which provider and model each review step uses. */
export function ModelRoutesCard({
  routes,
  providers,
  canEdit,
}: {
  routes: ModelRoutePublic[];
  providers: ProviderPublic[];
  canEdit: boolean;
}) {
  // Initialised from the saved routes; the parent re-mounts this card (via
  // `key`) whenever they change, so there is no prop-to-state syncing.
  const [draft, setDraft] = useState<Draft>(() => draftFrom(routes));
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: () => {
      const body: RouteUpdate = {};
      for (const { tier } of TIERS) {
        const entry = draft[tier];
        body[tier] =
          entry.providerId === NONE || !entry.model.trim()
            ? null
            : { provider_id: entry.providerId, model: entry.model.trim() };
      }
      return setModelRoutes(body);
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(["model-routes"], updated);
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
  });
  const reviewSet = routes.some((r) => r.tier === "review");
  const providerName = (id: string) => providers.find((p) => p.id === id)?.name ?? "Choose a provider";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Model for each review step</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {!reviewSet && (
          <p role="status" className="text-sm text-amber-700 dark:text-amber-400">
            No review model yet: reviews are refused until you choose one below.
          </p>
        )}
        {TIERS.map(({ tier, label, description, inUse }) => (
          <div key={tier} className="grid gap-2 sm:grid-cols-[10rem_1fr_1fr] sm:items-start">
            <div className="flex flex-col">
              <span className="flex items-center gap-2 text-sm font-medium">
                {label}
                {!inUse && <Badge variant="outline">not used yet</Badge>}
              </span>
              <span className="text-xs text-muted-foreground">{description}</span>
            </div>
            <Select
              value={draft[tier].providerId}
              disabled={!canEdit}
              onValueChange={(value) =>
                setDraft((d) => ({ ...d, [tier]: { ...d[tier], providerId: String(value ?? NONE) } }))
              }
            >
              <SelectTrigger aria-label={`${label} provider`} className="w-full">
                <SelectValue>
                  {(value: string) => (value === NONE ? "Not set" : providerName(value))}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>Not set</SelectItem>
                {providers.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              aria-label={`${label} model`}
              disabled={!canEdit || draft[tier].providerId === NONE}
              value={draft[tier].model}
              onChange={(event) =>
                setDraft((d) => ({ ...d, [tier]: { ...d[tier], model: event.target.value } }))
              }
              placeholder="model id, e.g. qwen3.5:9b"
            />
          </div>
        ))}
        {save.isError && (
          <p role="alert" className="text-sm text-destructive">
            {formatApiError(save.error)}
          </p>
        )}
        {canEdit ? (
          <div className="flex items-center gap-3">
            <Button disabled={save.isPending} onClick={() => save.mutate()}>
              {save.isPending ? "Saving…" : "Save models"}
            </Button>
            {save.isSuccess && <span className="text-sm text-muted-foreground">Saved.</span>}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">Only owners and admins can change models.</p>
        )}
      </CardContent>
    </Card>
  );
}
