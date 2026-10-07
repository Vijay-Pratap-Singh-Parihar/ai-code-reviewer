"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, FolderGit2, Plug } from "lucide-react";
import {
  deleteRepositoryData,
  formatApiError,
  listRepositories,
  type RepositoryPublic,
} from "@/lib/api-client";
import { formatDate } from "@/lib/lifecycle";
import { isOrgAdmin } from "@/lib/permissions";
import { useAuth } from "@/components/auth-provider";
import { AutoReviewToggle } from "@/components/auto-review-toggle";
import { DeleteDataDialog } from "@/components/delete-data-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export function useRepositories() {
  return useQuery({ queryKey: ["repositories"], queryFn: listRepositories });
}

export function RepositoryRow({ repo, compact }: { repo: RepositoryPublic; compact: boolean }) {
  const { user } = useAuth();
  const admin = isOrgAdmin(user);
  const linkable = repo.connected && repo.is_active;
  // Data can be deleted once GitHub can no longer sync it back: disconnected,
  // or only ever created from a pasted diff.
  const deletable = admin && !compact && (Boolean(repo.disconnected_at) || repo.github_repo_id === null);
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="flex min-w-0 flex-col gap-0.5">
      <div className="flex min-w-0 items-center gap-2">
        <FolderGit2 className="size-4 shrink-0 text-muted-foreground" />
        {linkable ? (
          <Link href={`/repositories/${repo.id}`} className="truncate font-medium hover:underline">
            {repo.full_name}
          </Link>
        ) : (
          <span className="truncate font-medium text-muted-foreground">{repo.full_name}</span>
        )}
        <Badge variant="outline" className="font-mono text-xs">
          {repo.default_branch}
        </Badge>
        {repo.github_repo_id === null && <Badge variant="secondary">manual</Badge>}
        {/* Came from GitHub, but the App was uninstalled or lost access to it. */}
        {repo.github_repo_id !== null && repo.disconnected_at && <Badge variant="secondary">disconnected</Badge>}
        {/* Still connected, but paused (the installation is suspended). */}
        {repo.github_repo_id !== null && !repo.disconnected_at && !repo.is_active && (
          <Badge variant="secondary">paused</Badge>
        )}
      </div>
      {repo.purge_after && !compact && (
        <span className="pl-6 text-xs text-muted-foreground">
          Data deleted automatically from {formatDate(repo.purge_after)} unless it&apos;s connected again.
        </span>
      )}
      </div>
      <div className="flex items-center gap-3">
        {linkable && <AutoReviewToggle repo={repo} canEdit={admin} />}
        {deletable && (
          <DeleteDataDialog
            name={repo.full_name}
            description={
              <>
                Permanently deletes this repository&apos;s pull request reviews, findings, branch indexes
                and cloned code. The audit log keeps a record that it was deleted. This cannot be undone.
              </>
            }
            onDelete={() => deleteRepositoryData(repo.id)}
          />
        )}
        {linkable && !compact && (
          <Button variant="ghost" size="sm" render={<Link href={`/repositories/${repo.id}`} />}>
            Pull requests
            <ChevronRight />
          </Button>
        )}
      </div>
    </li>
  );
}

/**
 * The org's repositories. Connected (GitHub App) repos come first and can be
 * opened and auto-reviewed; repos that only exist from the manual paste-a-diff
 * flow are listed but greyed out, since there's no GitHub access to use.
 */
export function RepositoryList({ limit, compact = false }: { limit?: number; compact?: boolean }) {
  const { data, isPending, isError, error } = useRepositories();

  if (isPending) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {formatApiError(error)}
      </p>
    );
  }

  const connected = data.filter((r) => r.connected);
  if (connected.length === 0) {
    return (
      <div className="flex flex-col items-start gap-3">
        <p className="text-sm text-muted-foreground">
          No GitHub repositories connected yet. Connect a GitHub account to review pull requests with one
          click, no diff pasting needed.
        </p>
        <Button render={<Link href="/github" />}>
          <Plug />
          Connect GitHub
        </Button>
      </div>
    );
  }

  const rows = limit === undefined ? data : data.slice(0, limit);
  return (
    <div className="flex flex-col gap-2">
      <ul className="divide-y">
        {rows.map((repo) => (
          <RepositoryRow key={repo.id} repo={repo} compact={compact} />
        ))}
      </ul>
      {limit !== undefined && data.length > limit && (
        <Link href="/repositories" className="text-sm text-muted-foreground underline underline-offset-4">
          View all {data.length} repositories
        </Link>
      )}
    </div>
  );
}
