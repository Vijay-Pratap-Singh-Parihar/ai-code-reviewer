"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Send } from "lucide-react";
import { formatApiError, postReviewToGitHub, type AnalysisRunPublic } from "@/lib/api-client";
import { Button, buttonVariants } from "@/components/ui/button";

const POLL_MS = 2500;
const MAX_POLLS = 24; // about a minute

/**
 * Publish a finished pull request review on GitHub, as one review comment
 * (never an approval or a block). Automatic reviews are posted by the
 * worker on their own; manual ones wait for this button so someone can
 * read the findings first. Shows the posted review's link once it exists,
 * and why posting failed when it did (e.g. the App lacks write access).
 */
export function PostToGitHub({ run }: { run: AnalysisRunPublic }) {
  const queryClient = useQueryClient();
  const [polling, setPolling] = useState(false);
  const polls = useRef(0);
  const post = useMutation({
    mutationFn: () => postReviewToGitHub(run.id),
    onSuccess: (updated) => {
      queryClient.setQueryData(["analysis-run", run.id], updated);
      polls.current = 0;
      setPolling(true);
    },
  });

  const settled = Boolean(run.github_review_url) || Boolean(run.post_error);
  useEffect(() => {
    if (!polling) return;
    if (settled || polls.current >= MAX_POLLS) {
      setPolling(false);
      return;
    }
    const timer = setTimeout(() => {
      polls.current += 1;
      void queryClient.invalidateQueries({ queryKey: ["analysis-run", run.id] });
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [polling, settled, run, queryClient]);

  if (run.source !== "github" || run.status !== "succeeded") return null;

  if (run.github_review_url) {
    return (
      <a href={run.github_review_url} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "outline", size: "sm" })}>
        Posted to GitHub
        <ExternalLink />
      </a>
    );
  }

  const busy = post.isPending || polling;
  return (
    <div className="flex flex-col gap-1">
      <div>
        <Button size="sm" disabled={busy} onClick={() => post.mutate()}>
          <Send />
          {busy ? "Posting to GitHub…" : run.post_error ? "Try posting again" : "Post review to GitHub"}
        </Button>
      </div>
      {!busy && run.post_error && (
        <p role="alert" className="text-xs text-destructive">
          Couldn&apos;t post: {run.post_error}
        </p>
      )}
      {post.isError && (
        <p role="alert" className="text-xs text-destructive">
          {formatApiError(post.error)}
        </p>
      )}
    </div>
  );
}
