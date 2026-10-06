import { useSyncExternalStore } from "react";
import type { Agent } from "@/lib/api-client";

/**
 * Since Stage 10 the API stores each run's diff and PR context and returns
 * them from `GET /analysis/{id}`, which the run page prefers. This store is
 * now only a fallback for runs created before that. Original rationale:
 *
 * The diff a run was triggered with is never persisted server-side —
 * `AnalysisRequest.diff` is a transient job argument (see its docstring),
 * not a column on `AnalysisRun` — so the PR analysis view has nowhere to
 * read it back from. Rather than adding a new backend column/endpoint for
 * what is, today, purely a same-session convenience, the trigger form
 * stashes what it already has in hand into `sessionStorage` here, keyed by
 * the run id `POST /analysis` hands back.
 *
 * Deliberate, documented limitation: this means the diff view is only
 * available for runs triggered in the current browser tab's session (lost
 * on tab close, and never available to a different tab/device) — the same
 * "session-only" simplification already made for the dashboard's run list.
 * Findings and their evidence trail are unaffected: those come from the
 * real API response, not this store.
 */
export type RunMetadata = {
  repoFullName: string;
  baseBranch: string;
  prNumber: number;
  prTitle: string;
  prBody: string;
  diff: string;
  agent: Agent;
};

const KEY_PREFIX = "revu:run-metadata:";

export function saveRunMetadata(runId: string, metadata: RunMetadata): void {
  try {
    sessionStorage.setItem(KEY_PREFIX + runId, JSON.stringify(metadata));
  } catch {
    // Private browsing / storage quota / disabled storage — losing the diff
    // preview is a degraded-but-safe fallback, not something to surface as
    // an error to a user who just successfully triggered a real review.
  }
}

export function getRunMetadata(runId: string): RunMetadata | null {
  try {
    const raw = sessionStorage.getItem(KEY_PREFIX + runId);
    return raw ? (JSON.parse(raw) as RunMetadata) : null;
  } catch {
    return null;
  }
}

// `useSyncExternalStore` requires `getSnapshot` to return a referentially
// stable value when the underlying data hasn't changed — a fresh
// `JSON.parse()` on every call (what plain `getRunMetadata` does) returns a
// new object each time even for identical content, which `useRunMetadata`
// below found the hard way: it re-renders forever rather than settling.
// This cache makes the snapshot stable across calls for the same raw string.
const parsedSnapshotCache = new Map<string, { raw: string; value: RunMetadata }>();

function getRunMetadataSnapshot(runId: string): RunMetadata | null {
  let raw: string | null;
  try {
    raw = sessionStorage.getItem(KEY_PREFIX + runId);
  } catch {
    raw = null;
  }
  if (raw === null) {
    parsedSnapshotCache.delete(runId);
    return null;
  }

  const cached = parsedSnapshotCache.get(runId);
  if (cached && cached.raw === raw) return cached.value;

  try {
    const value = JSON.parse(raw) as RunMetadata;
    parsedSnapshotCache.set(runId, { raw, value });
    return value;
  } catch {
    return null;
  }
}

const noopSubscribe = () => () => {};

/**
 * `sessionStorage` isn't available during SSR, and this value is read once
 * right after navigating to the run page and never changes while it's
 * mounted (no cross-tab sync needed) — `useSyncExternalStore` with a no-op
 * subscribe and a `null` server snapshot reads it safely after hydration
 * without the "setState inside an effect" anti-pattern a plain
 * `useState`/`useEffect` pair would need for the same thing.
 */
export function useRunMetadata(runId: string): RunMetadata | null {
  return useSyncExternalStore(
    noopSubscribe,
    () => getRunMetadataSnapshot(runId),
    () => null,
  );
}
