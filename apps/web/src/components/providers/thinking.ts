import type { ProviderKind, Reasoning } from "@/lib/api-client";

/** The select value for "leave it to the model" (`null` in the API). */
export const MODEL_DEFAULT = "default";

export const THINKING: { value: Reasoning | typeof MODEL_DEFAULT; label: string }[] = [
  { value: MODEL_DEFAULT, label: "Model default" },
  { value: "off", label: "Off (fastest)" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
];

export const thinkingLabel = (value: string) => THINKING.find((t) => t.value === value)?.label ?? value;

export const toReasoning = (value: string | null): Reasoning | null =>
  !value || value === MODEL_DEFAULT ? null : (value as Reasoning);

/**
 * What to test with before the user picks: self-hosted open models (Qwen,
 * DeepSeek) think at length by default, which on a laptop GPU turns a
 * three-call test into minutes; hosted APIs are fast either way.
 */
export const defaultTestThinking = (kind: ProviderKind): Reasoning | null =>
  kind === "openai_compatible" ? "off" : null;
