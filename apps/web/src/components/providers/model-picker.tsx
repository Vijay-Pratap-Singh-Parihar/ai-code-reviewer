"use client";

import { Autocomplete } from "@base-ui/react/autocomplete";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, Check, ChevronDown, Loader2, RefreshCw } from "lucide-react";
import { cn } from "cn";
import { formatApiError, listProviderModels } from "@/lib/api-client";

/**
 * A model id field that suggests the models the provider says it serves.
 * The list loads on its own as soon as a provider is chosen; any id can
 * still be typed, since some servers don't list every model they run.
 */
export function ModelPicker({
  providerId,
  value,
  onValueChange,
  id,
  ariaLabel,
  disabled = false,
  placeholder = "Choose or type a model id",
}: {
  providerId: string | null;
  value: string;
  onValueChange: (value: string) => void;
  id?: string;
  ariaLabel?: string;
  disabled?: boolean;
  placeholder?: string;
}) {
  const models = useQuery({
    queryKey: ["provider-models", providerId],
    queryFn: () => listProviderModels(providerId as string),
    enabled: Boolean(providerId) && !disabled,
    staleTime: 5 * 60_000,
    retry: false,
  });
  const items = models.data?.models ?? [];

  return (
    <Autocomplete.Root
      items={items}
      value={value}
      onValueChange={(next) => onValueChange(next)}
      openOnInputClick
      disabled={disabled}
    >
      <Autocomplete.InputGroup
        className={cn(
          "flex h-8 w-full min-w-0 items-center rounded-lg border border-input bg-transparent transition-colors focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50 dark:bg-input/30",
          disabled && "pointer-events-none cursor-not-allowed opacity-50",
        )}
      >
        <Autocomplete.Input
          id={id}
          aria-label={ariaLabel}
          placeholder={placeholder}
          autoComplete="off"
          spellCheck={false}
          className="h-full min-w-0 flex-1 bg-transparent px-2.5 text-base outline-none placeholder:text-muted-foreground md:text-sm"
        />
        {models.isFetching ? (
          <Loader2 aria-label="Loading models" className="mr-2 size-4 shrink-0 animate-spin text-muted-foreground" />
        ) : (
          <Autocomplete.Trigger
            aria-label="Show models"
            className="mr-1 flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <ChevronDown className="size-4" />
          </Autocomplete.Trigger>
        )}
      </Autocomplete.InputGroup>

      <Autocomplete.Portal>
        <Autocomplete.Positioner sideOffset={4} className="isolate z-50 outline-none">
          <Autocomplete.Popup className="w-(--anchor-width) max-w-(--available-width) min-w-56 origin-(--transform-origin) overflow-hidden rounded-lg bg-popover text-popover-foreground shadow-md ring-1 ring-foreground/10 duration-100 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
            <ListStatus
              loading={models.isFetching}
              error={models.isError ? formatApiError(models.error) : null}
              count={items.length}
              onRetry={() => void models.refetch()}
            />
            <Autocomplete.Empty>
              {items.length > 0 && (
                <p className="px-3 py-3 text-sm text-muted-foreground">
                  No listed model matches. {value.trim() && <>It will use “{value.trim()}” as typed.</>}
                </p>
              )}
            </Autocomplete.Empty>
            <Autocomplete.List className="max-h-[min(18rem,var(--available-height))] scroll-py-1 overflow-y-auto overscroll-contain p-1 data-empty:p-0">
              {(model: string) => (
                <Autocomplete.Item
                  key={model}
                  value={model}
                  className="flex cursor-default items-center gap-2 rounded-md py-1.5 pr-2 pl-2 font-mono text-sm outline-none select-none data-highlighted:bg-accent data-highlighted:text-accent-foreground"
                >
                  <span className="flex-1 truncate">{model}</span>
                  {model === value && <Check className="size-4 shrink-0 text-muted-foreground" />}
                </Autocomplete.Item>
              )}
            </Autocomplete.List>
          </Autocomplete.Popup>
        </Autocomplete.Positioner>
      </Autocomplete.Portal>
    </Autocomplete.Root>
  );
}

function ListStatus({
  loading,
  error,
  count,
  onRetry,
}: {
  loading: boolean;
  error: string | null;
  count: number;
  onRetry: () => void;
}) {
  if (loading) {
    return (
      <p className="flex items-center gap-2 border-b px-3 py-2 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        Asking the provider which models it serves…
      </p>
    );
  }
  if (error) {
    return (
      <div className="flex items-start gap-2 border-b px-3 py-2 text-xs">
        <AlertCircle className="mt-0.5 size-3.5 shrink-0 text-destructive" />
        <span className="flex-1 text-muted-foreground">
          Couldn&apos;t list models ({error}). You can still type the model id.
        </span>
        <button
          type="button"
          onClick={onRetry}
          className="flex items-center gap-1 font-medium text-foreground hover:underline"
        >
          <RefreshCw className="size-3" /> Retry
        </button>
      </div>
    );
  }
  return (
    <p className="border-b px-3 py-2 text-xs text-muted-foreground">
      {count === 0
        ? "This provider lists no models. Type the model id."
        : `${count} model${count === 1 ? "" : "s"} available`}
    </p>
  );
}
