"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Circle, FlaskConical, Loader2, MinusCircle, Play, Square, XCircle } from "lucide-react";
import {
  formatApiError,
  setModelRoutes,
  testProviderLive,
  type ProbeResult,
  type ProviderPublic,
  type ProviderTestResult,
  type Reasoning,
  type TestStep,
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
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ModelPicker } from "@/components/providers/model-picker";
import {
  MODEL_DEFAULT,
  THINKING,
  defaultTestThinking,
  thinkingLabel,
  toReasoning,
} from "@/components/providers/thinking";

const STEPS: { step: TestStep; label: string; hint: string }[] = [
  { step: "reply", label: "Replies", hint: "A one-word answer: the model is reachable and answers." },
  { step: "json_mode", label: "Returns JSON", hint: "Reviews are read as JSON, so this one is required." },
  {
    step: "tool_calling",
    label: "Calls tools",
    hint: "Needed for cross-file reviews; without it they run diff-only.",
  },
];

type StepStatus = "waiting" | "running" | "passed" | "failed" | "skipped";
type StepState = { status: StepStatus; startedAt?: number; result?: ProbeResult };
type Progress = Record<TestStep, StepState>;

const fresh = (): Progress => ({
  reply: { status: "waiting" },
  json_mode: { status: "waiting" },
  tool_calling: { status: "waiting" },
});

const seconds = (ms: number) => `${(ms / 1000).toFixed(1)} s`;

/** Ticks while `active`, so running timers update on screen. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

/**
 * "Test connection": three tiny real calls (a reply, JSON mode, a tool call),
 * shown live as each runs. What passes decides how reviews use the provider:
 * JSON is required, and a model that can't call tools gets diff-only instead
 * of cross-file reviews.
 */
export function ProviderTestDialog({ provider }: { provider: ProviderPublic }) {
  const [open, setOpen] = useState(false);
  const [model, setModel] = useState("");
  const [thinking, setThinking] = useState<Reasoning | null>(defaultTestThinking(provider.kind));
  const [progress, setProgress] = useState<Progress>(fresh);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [finishedAt, setFinishedAt] = useState<number | null>(null);
  const abort = useRef<AbortController | null>(null);
  const queryClient = useQueryClient();

  const test = useMutation({
    mutationFn: async (): Promise<ProviderTestResult> => {
      abort.current = new AbortController();
      setProgress(fresh());
      setStartedAt(Date.now());
      setFinishedAt(null);
      return testProviderLive(
        provider.id,
        { model: model.trim(), reasoning: thinking },
        (event) => {
          if (event.event === "start") {
            setProgress((p) => ({ ...p, [event.step]: { status: "running", startedAt: Date.now() } }));
          } else if (event.event === "step") {
            const skipped = !event.result.ok && Boolean(event.result.detail?.startsWith("skipped"));
            setProgress((p) => ({
              ...p,
              [event.step]: {
                ...p[event.step],
                status: event.result.ok ? "passed" : skipped ? "skipped" : "failed",
                result: event.result,
              },
            }));
          }
        },
        abort.current.signal,
      );
    },
    onSettled: () => {
      setFinishedAt(Date.now());
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
  });

  const adopt = useMutation({
    mutationFn: () =>
      setModelRoutes({ review: { provider_id: provider.id, model: model.trim(), reasoning: thinking } }),
    onSuccess: (routes) => {
      queryClient.setQueryData(["model-routes"], routes);
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
  });

  const running = test.isPending;
  const now = useNow(running);
  const stopped = test.error?.name === "AbortError";

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    adopt.reset();
    test.mutate();
  }

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (!next) abort.current?.abort();
    if (next) {
      test.reset();
      adopt.reset();
      setProgress(fresh());
      setStartedAt(null);
    }
  }

  const started = startedAt !== null;
  const total = started ? (finishedAt ?? now) - startedAt : 0;
  const finishedSteps = STEPS.filter(({ step }) => !["waiting", "running"].includes(progress[step].status)).length;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger render={<Button variant="outline" size="sm" />}>
        <FlaskConical />
        Test
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Test {provider.name}</DialogTitle>
          <DialogDescription>
            Three small real calls to the model. Paid providers bill them (a fraction of a cent). A local
            model loads into memory on its first call, which can take a minute.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="grid gap-3 sm:grid-cols-[1fr_11rem]">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`test-model-${provider.id}`}>Model</Label>
              <ModelPicker
                id={`test-model-${provider.id}`}
                providerId={provider.id}
                value={model}
                onValueChange={setModel}
                disabled={running}
                placeholder={provider.kind === "openai_compatible" ? "e.g. qwen3.5:4b" : "Choose or type a model id"}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label id={`test-thinking-${provider.id}`}>Thinking</Label>
              <Select
                value={thinking ?? MODEL_DEFAULT}
                disabled={running}
                onValueChange={(value) => setThinking(toReasoning(value))}
              >
                <SelectTrigger aria-labelledby={`test-thinking-${provider.id}`} className="w-full">
                  <SelectValue>{(value: string) => thinkingLabel(value)}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {THINKING.map((t) => (
                    <SelectItem key={t.value} value={t.value}>
                      {t.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <p className="-mt-2 text-xs text-muted-foreground">
            Test with the thinking level you&apos;ll review with. Local models such as Qwen think by default,
            which can make each call take minutes on a small GPU.
          </p>

          <div className="flex items-center gap-3">
            {running ? (
              <Button type="button" variant="outline" onClick={() => abort.current?.abort()}>
                <Square className="fill-current" />
                Stop
              </Button>
            ) : (
              <Button type="submit" disabled={!model.trim()}>
                <Play />
                {started ? "Run again" : "Run test"}
              </Button>
            )}
            {started && (
              <span className="text-sm text-muted-foreground tabular-nums" aria-live="off">
                {running ? "Running" : "Took"} {seconds(total)}
              </span>
            )}
          </div>
        </form>

        {started && (
          <section aria-label="Test progress" className="flex flex-col gap-3 rounded-lg border p-3">
            <div
              role="progressbar"
              aria-label="Calls finished"
              aria-valuemin={0}
              aria-valuemax={STEPS.length}
              aria-valuenow={finishedSteps}
              className="h-1.5 overflow-hidden rounded-full bg-muted"
            >
              <div
                className={`h-full rounded-full transition-[width] duration-500 ${
                  test.data && !test.data.ok ? "bg-destructive" : "bg-emerald-500"
                } ${running ? "animate-pulse" : ""}`}
                style={{ width: `${Math.max(running ? 4 : 0, (finishedSteps / STEPS.length) * 100)}%` }}
              />
            </div>
            <ol className="flex flex-col gap-3">
              {STEPS.map(({ step, label, hint }) => (
                <StepRow key={step} label={label} hint={hint} state={progress[step]} now={now} />
              ))}
            </ol>
          </section>
        )}

        {test.isError && (
          <p role="alert" className="text-sm text-destructive">
            {stopped ? "Stopped. Nothing was recorded." : formatApiError(test.error)}
          </p>
        )}
        {test.data && (
          <Outcome
            result={test.data}
            usingIt={adopt.isSuccess}
            saving={adopt.isPending}
            saveError={adopt.isError ? formatApiError(adopt.error) : null}
            onUse={() => adopt.mutate()}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function StepRow({ label, hint, state, now }: { label: string; hint: string; state: StepState; now: number }) {
  const { status, result, startedAt } = state;
  const icon = {
    waiting: <Circle className="size-4 text-muted-foreground/50" aria-label="waiting" />,
    running: <Loader2 className="size-4 animate-spin text-primary" aria-label="running" />,
    passed: <CheckCircle2 className="size-4 text-emerald-600 dark:text-emerald-400" aria-label="passed" />,
    failed: <XCircle className="size-4 text-destructive" aria-label="failed" />,
    skipped: <MinusCircle className="size-4 text-muted-foreground" aria-label="skipped" />,
  }[status];
  const time =
    status === "running" && startedAt
      ? seconds(now - startedAt)
      : result?.latency_ms != null
        ? seconds(result.latency_ms)
        : null;
  const slow = status === "running" && startedAt !== undefined && now - startedAt > 8000;

  return (
    <li className="flex items-start gap-3">
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-baseline justify-between gap-2">
          <span className={`text-sm font-medium ${status === "waiting" ? "text-muted-foreground" : ""}`}>
            {label}
          </span>
          {time && <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{time}</span>}
        </div>
        <span className="text-xs text-muted-foreground">
          {status === "failed" || status === "skipped"
            ? result?.detail
            : slow
              ? "Still working: a local model may be loading into memory or thinking."
              : status === "passed" && result?.detail
                ? `Answered “${result.detail}”`
                : hint}
        </span>
      </div>
    </li>
  );
}

function Outcome({
  result,
  usingIt,
  saving,
  saveError,
  onUse,
}: {
  result: ProviderTestResult;
  usingIt: boolean;
  saving: boolean;
  saveError: string | null;
  onUse: () => void;
}) {
  if (!result.ok) {
    return (
      <div role="status" className="flex gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm">
        <XCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
        <p>
          <span className="font-medium">Not usable for reviews yet.</span> The model must reply and return
          JSON. Check the endpoint and model id, or try another model.
        </p>
      </div>
    );
  }
  return (
    <div
      role="status"
      className="flex flex-col gap-3 rounded-lg border border-emerald-600/30 bg-emerald-500/5 p-3 text-sm sm:flex-row sm:items-center"
    >
      <div className="flex flex-1 gap-2">
        <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
        <p>
          <span className="font-medium">Ready for reviews.</span>{" "}
          {result.tool_calling.ok
            ? "It can do cross-file reviews too."
            : "Without tool calling, its cross-file reviews run as diff-only."}
          {saveError && <span className="block text-destructive">{saveError}</span>}
        </p>
      </div>
      {usingIt ? (
        <span className="flex items-center gap-1.5 font-medium text-emerald-700 dark:text-emerald-400">
          <CheckCircle2 className="size-4" /> Reviews now use {result.model}
        </span>
      ) : (
        <Button size="sm" onClick={onUse} disabled={saving}>
          {saving && <Loader2 className="animate-spin" />}
          Use for reviews
        </Button>
      )}
    </div>
  );
}
