"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ExternalLink, GitPullRequest } from "lucide-react";
import {
  formatApiError,
  listPullRequests,
  reviewPullRequest,
  type Agent,
  type PullRequestSummary,
} from "@/lib/api-client";
import { STATUS_BADGE } from "@/lib/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

function PullRequestRow({
  repoId,
  pull,
  agent,
}: {
  repoId: string;
  pull: PullRequestSummary;
  agent: Agent;
}) {
  const router = useRouter();
  const review = useMutation({
    mutationFn: () => reviewPullRequest(repoId, pull.number, agent),
    onSuccess: (run) => router.push(`/runs/${run.id}`),
  });
  const run = pull.latest_run;
  const runIsCurrent = run !== null && run.head_sha === pull.head_sha;

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="flex min-w-0 flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <GitPullRequest className="size-4 shrink-0 text-muted-foreground" />
          <span className="truncate font-medium">
            #{pull.number} {pull.title}
          </span>
          {pull.draft && <Badge variant="secondary">draft</Badge>}
          {pull.html_url && (
            <a
              href={pull.html_url}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open PR #${pull.number} on GitHub`}
              className="text-muted-foreground hover:text-foreground"
            >
              <ExternalLink className="size-3.5" />
            </a>
          )}
        </div>
        <span className="text-xs text-muted-foreground">
          {pull.author} · into <span className="font-mono">{pull.base_branch}</span> · head{" "}
          <span className="font-mono">{pull.head_sha.slice(0, 7)}</span>
        </span>
        {review.isError && (
          <span role="alert" className="text-xs text-destructive">
            {formatApiError(review.error)}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        {run && (
          <Link href={`/runs/${run.id}`} className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Badge variant={STATUS_BADGE[run.status].variant} className={STATUS_BADGE[run.status].className}>
              {run.status}
            </Badge>
            {runIsCurrent ? "latest commit" : "older commit"}
          </Link>
        )}
        <Button size="sm" disabled={review.isPending} onClick={() => review.mutate()}>
          {review.isPending ? "Queuing…" : run ? "Review again" : "Review"}
        </Button>
      </div>
    </li>
  );
}

export function PullRequestList({ repoId, hasReadyIndex }: { repoId: string; hasReadyIndex: boolean }) {
  const [agent, setAgent] = useState<Agent>("diff_only");
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["pulls", repoId],
    queryFn: () => listPullRequests(repoId),
  });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span id="pr-agent-label" className="text-sm">
          Review depth
        </span>
        <Select value={agent} onValueChange={(value) => setAgent(value as Agent)}>
          <SelectTrigger aria-labelledby="pr-agent-label" className="w-72">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="diff_only">diff_only: cheap, diff text only</SelectItem>
            <SelectItem value="cross_file" disabled={!hasReadyIndex}>
              cross_file: reads the repo (~4x cost){hasReadyIndex ? "" : " · build an index first"}
            </SelectItem>
          </SelectContent>
        </Select>
      </div>
      <p className="text-xs text-muted-foreground">Each review makes a billed LLM call.</p>

      {isPending ? (
        <p className="text-sm text-muted-foreground">Loading pull requests from GitHub…</p>
      ) : isError ? (
        <p role="alert" className="text-sm text-destructive">
          {formatApiError(error)}
        </p>
      ) : data.length === 0 ? (
        <p className="text-sm text-muted-foreground">No open pull requests.</p>
      ) : (
        <ul className="divide-y">
          {data.map((pull) => (
            <PullRequestRow key={pull.number} repoId={repoId} pull={pull} agent={agent} />
          ))}
        </ul>
      )}
    </div>
  );
}
