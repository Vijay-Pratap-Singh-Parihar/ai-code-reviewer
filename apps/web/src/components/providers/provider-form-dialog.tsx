"use client";

import { useState, type FormEvent, type ReactElement } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  createProvider,
  formatApiError,
  updateProvider,
  type ProviderCreate,
  type ProviderKind,
  type ProviderKindInfo,
  type ProviderPublic,
  type ProviderUpdate,
} from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

/** Common OpenAI-compatible servers, as reached from revu's API and worker containers. */
export const ENDPOINT_PRESETS: { label: string; url: string }[] = [
  { label: "Ollama on this machine", url: "http://host.docker.internal:11434/v1" },
  { label: "LM Studio on this machine", url: "http://host.docker.internal:1234/v1" },
  { label: "vLLM server", url: "http://vllm.internal:8000/v1" },
];

function numberOrNull(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Add a provider, or edit one. Keys are write-only: when editing, the key
 * field starts empty and leaving it empty keeps the stored key (shown only
 * by its hint); a new value replaces it. Keys go to the API over HTTPS and
 * are stored encrypted; nothing here keeps them after the dialog closes.
 */
export function ProviderFormDialog({
  kinds,
  provider,
  trigger,
}: {
  kinds: ProviderKindInfo[];
  provider?: ProviderPublic;
  trigger: ReactElement;
}) {
  const editing = provider !== undefined;
  const firstAvailable = kinds.find((k) => k.available)?.kind ?? "anthropic";
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<ProviderKind>(provider?.kind ?? firstAvailable);
  const [name, setName] = useState(provider?.name ?? "");
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [inputPrice, setInputPrice] = useState(String(provider?.settings.input_cost_per_mtok ?? ""));
  const [outputPrice, setOutputPrice] = useState(String(provider?.settings.output_cost_per_mtok ?? ""));
  const info = kinds.find((k) => k.kind === kind);
  const queryClient = useQueryClient();

  function reset() {
    setKind(provider?.kind ?? firstAvailable);
    setName(provider?.name ?? "");
    setBaseUrl(provider?.base_url ?? "");
    setApiKey("");
    setClearKey(false);
    setInputPrice(String(provider?.settings.input_cost_per_mtok ?? ""));
    setOutputPrice(String(provider?.settings.output_cost_per_mtok ?? ""));
    save.reset();
  }

  const save = useMutation({
    mutationFn: () => {
      const settings = {
        ...(provider?.settings ?? {}),
        input_cost_per_mtok: numberOrNull(inputPrice),
        output_cost_per_mtok: numberOrNull(outputPrice),
      };
      if (editing) {
        const body: ProviderUpdate = { name: name.trim(), settings };
        if (info?.needs_base_url) body.base_url = baseUrl.trim();
        if (clearKey) body.api_key = "";
        else if (apiKey.trim()) body.api_key = apiKey.trim();
        return updateProvider(provider.id, body);
      }
      const body: ProviderCreate = {
        name: name.trim(),
        kind,
        api_key: apiKey.trim() || null,
        settings,
      };
      if (info?.needs_base_url) body.base_url = baseUrl.trim();
      return createProvider(body);
    },
    onSuccess: () => {
      setOpen(false);
      setApiKey("");
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
      void queryClient.invalidateQueries({ queryKey: ["model-routes"] });
    },
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    save.mutate();
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) reset();
      }}
    >
      <DialogTrigger render={trigger} />
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{editing ? `Edit ${provider.name}` : "Add an AI provider"}</DialogTitle>
          <DialogDescription>
            Keys are stored encrypted and are never shown again, only their last characters.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          {!editing && (
            <div className="flex flex-col gap-1.5">
              <span id="provider-kind-label" className="text-sm font-medium">
                Provider
              </span>
              <Select value={kind} onValueChange={(value) => setKind(value as ProviderKind)}>
                <SelectTrigger aria-labelledby="provider-kind-label" className="w-full">
                  <SelectValue>{() => info?.label ?? kind}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {kinds.map((k) => (
                    <SelectItem key={k.kind} value={k.kind} disabled={!k.available}>
                      {k.label}
                      {!k.available && " (coming soon)"}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="provider-name">Name</Label>
            <Input
              id="provider-name"
              required
              maxLength={100}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={kind === "openai_compatible" ? "Local Ollama" : info?.label}
            />
          </div>

          {info?.needs_base_url && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="provider-url">Endpoint URL</Label>
              <Input
                id="provider-url"
                required
                type="url"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="http://host.docker.internal:11434/v1"
              />
              <div className="flex flex-wrap gap-1.5">
                {ENDPOINT_PRESETS.map((preset) => (
                  <Button
                    key={preset.url}
                    type="button"
                    variant="outline"
                    size="xs"
                    onClick={() => setBaseUrl(preset.url)}
                  >
                    {preset.label}
                  </Button>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                Any server with an OpenAI-compatible <code>/v1/chat/completions</code> API: Ollama, vLLM,
                LM Studio, llama.cpp, NVIDIA NIM, or your company&apos;s own fine-tuned model.
              </p>
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="provider-key">
              API key{info && !info.needs_api_key ? " (optional)" : ""}
            </Label>
            <Input
              id="provider-key"
              type="password"
              autoComplete="off"
              required={!editing && Boolean(info?.needs_api_key)}
              disabled={clearKey}
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder={
                editing && provider.key_hint ? `Leave empty to keep ${provider.key_hint}` : "Paste the key"
              }
            />
            {editing && provider.has_api_key && info && !info.needs_api_key && (
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <input type="checkbox" checked={clearKey} onChange={(event) => setClearKey(event.target.checked)} />
                Remove the stored key
              </label>
            )}
          </div>

          {kind === "openai_compatible" && (
            <fieldset className="flex flex-col gap-1.5">
              <legend className="mb-1.5 text-sm font-medium">Price per million tokens (optional, USD)</legend>
              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-1">
                  <Label htmlFor="price-in" className="text-xs font-normal text-muted-foreground">
                    Input
                  </Label>
                  <Input
                    id="price-in"
                    inputMode="decimal"
                    value={inputPrice}
                    onChange={(event) => setInputPrice(event.target.value)}
                    placeholder="0"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <Label htmlFor="price-out" className="text-xs font-normal text-muted-foreground">
                    Output
                  </Label>
                  <Input
                    id="price-out"
                    inputMode="decimal"
                    value={outputPrice}
                    onChange={(event) => setOutputPrice(event.target.value)}
                    placeholder="0"
                  />
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                Self-hosted models have no public price, so set what running it costs you to keep cost
                reporting meaningful. Leave empty for free.
              </p>
            </fieldset>
          )}

          {save.isError && (
            <p role="alert" className="text-sm text-destructive">
              {formatApiError(save.error)}
            </p>
          )}
          <DialogFooter>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? "Saving…" : editing ? "Save changes" : "Add provider"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
