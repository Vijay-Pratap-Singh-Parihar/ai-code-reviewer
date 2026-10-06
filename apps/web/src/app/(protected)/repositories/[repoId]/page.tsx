"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ExternalLink } from "lucide-react";
import { useRepositories } from "@/components/repository-list";
import { AutoReviewToggle } from "@/components/auto-review-toggle";
import { BranchIndexCard, useBranchIndexStatus } from "@/components/branch-index-card";
import { PullRequestList } from "@/components/pull-request-list";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { RepositoryPublic } from "@/lib/api-client";

function RepositoryDetail({ repo }: { repo: RepositoryPublic }) {
  const index = useBranchIndexStatus(repo);
  return (
    <>
      <div className="flex flex-col gap-1">
        <Link href="/repositories" className="text-sm text-muted-foreground underline underline-offset-4">
          &larr; All repositories
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-lg font-semibold">{repo.full_name}</h1>
          <a
            href={`https://github.com/${repo.full_name}`}
            target="_blank"
            rel="noreferrer"
            aria-label="Open on GitHub"
            className="text-muted-foreground hover:text-foreground"
          >
            <ExternalLink className="size-4" />
          </a>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Automatic review</CardTitle>
          </CardHeader>
          <CardContent>
            <AutoReviewToggle repo={repo} showHint />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Branch memory</CardTitle>
          </CardHeader>
          <CardContent>
            <BranchIndexCard repo={repo} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Open pull requests</CardTitle>
        </CardHeader>
        <CardContent>
          <PullRequestList repoId={repo.id} hasReadyIndex={Boolean(index.data?.has_ready_index)} />
        </CardContent>
      </Card>
    </>
  );
}

export default function RepositoryPage() {
  const { repoId } = useParams<{ repoId: string }>();
  const { data, isPending, isError } = useRepositories();
  const repo = data?.find((r) => r.id === repoId);

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 p-6">
      {isPending ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : isError || !repo || !repo.connected || !repo.is_active ? (
        <p className="text-sm text-destructive">
          Repository not found or not connected.{" "}
          <Link href="/repositories" className="underline underline-offset-4">
            Back to repositories
          </Link>
        </p>
      ) : (
        <RepositoryDetail repo={repo} />
      )}
    </div>
  );
}
