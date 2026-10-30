"use client";

import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FlaskConical, XCircle } from "lucide-react";
import {
  formatApiError,
  listProviderModels,
  testProvider,
  type ProbeResult,
  type ProviderPublic,
} from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

function ProbeRow({ label, result }: { label: string; result: ProbeResult }) {
  const Icon = result.ok ? CheckCircle2 : XCircle;
  return (
    <li className="flex items-start gap-2 text-sm">
      <Icon
        className={`mt-0.5 size-4 shrink-0 ${result.ok ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}`}
        aria-label={result.ok ? "passed" : "failed"}
      />
      <div className="flex flex-col">
        <span className="font-medium">
          {label}
          {result.latency_ms !== null && (
            <span className="font-normal text-muted-foreground"> · {(result.latency_ms / 1000).toFixed(1)} s</span>
          )}
        </span>
        {result.detail && <span className="break-all text-xs text-muted-foreground">{result.detail}</span>}
      </div>
    </li>
  );
}

/**
 * "Test connection": three tiny real calls (a reply, JSON mode, a tool call).
 * What passes decides how reviews use the provider: JSON is required, and a
 * model that can't call tools gets diff-only instead of cross-file reviews.
 */
export function ProviderTestDialog({ provider }: { provider: ProviderPublic }) {
  const [open, setOpen] = useState(false);
  const [model, setModel] = useState("");
  const queryClient = useQueryClient();
  const models = useMutation({ mutationFn: () => listProviderModels(provider.id) });
  const test = useMutation({
    mutationFn: () => testProvider(provider.id, model.trim()),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
  });
  const listId = `models-${provider.id}`;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    test.mutate();
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          test.reset();
          models.reset();
        }
      }}
    >
      <DialogTrigger render={<Button variant="outline" size="sm" />}>
        <FlaskConical />
        Test
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Test {provider.name}</DialogTitle>
          <DialogDescription>
            Makes three small real calls to the model. Paid providers bill them (a fraction of a cent). A
            local model may take a minute the first time while it loads.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`test-model-${provider.id}`}>Model</Label>
            <div className="flex gap-2">
              <Input
                id={`test-model-${provider.id}`}
                required
                list={listId}
                value={model}
                onChange={(event) => setModel(event.target.value)}
                placeholder={provider.kind === "openai_compatible" ? "qwen3.5:9b" : "model id"}
              />
              <Button type="button" variant="outline" disabled={models.isPending} onClick={() => models.mutate()}>
                {models.isPending ? "Loading…" : "List models"}
              </Button>
            </div>
            <datalist id={listId}>
              {models.data?.models.map((m) => <option key={m} value={m} />)}
            </datalist>
            {models.isSuccess && (
              <span className="text-xs text-muted-foreground">
                {models.data.models.length} models available; start typing to pick one.
              </span>
            )}
            {models.isError && (
              <span role="alert" className="text-xs text-destructive">
                {formatApiError(models.error)}
              </span>
            )}
          </div>
          <div>
            <Button type="submit" disabled={test.isPending || !model.trim()}>
              {test.isPending ? "Testing…" : "Run test"}
            </Button>
          </div>
        </form>

        {test.isError && (
          <p role="alert" className="text-sm text-destructive">
            {formatApiError(test.error)}
          </p>
        )}
        {test.data && (
          <div className="flex flex-col gap-2 rounded-lg border p-3">
            <p className="text-sm font-medium">
              {test.data.ok
                ? "Ready for reviews."
                : "Not usable for reviews yet: it must reply and return JSON."}
            </p>
            <ul className="flex flex-col gap-2">
              <ProbeRow label="Replies" result={test.data.reply} />
              <ProbeRow label="JSON output" result={test.data.json_mode} />
              <ProbeRow label="Tool calling (needed for cross-file reviews)" result={test.data.tool_calling} />
            </ul>
            {test.data.ok && !test.data.tool_calling.ok && (
              <p className="text-xs text-muted-foreground">
                Without tool calling, cross-file reviews with this model run as diff-only reviews instead.
              </p>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
