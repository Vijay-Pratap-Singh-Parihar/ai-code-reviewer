"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Plug } from "lucide-react";
import { formatApiError, listInstallations, type InstallationPublic, type RepositoryPublic } from "@/lib/api-client";
import { CONNECTION_STATUS_LABEL } from "@/lib/lifecycle";
import { RepositoryRow, useRepositories } from "@/components/repository-list";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Group = { key: string; installation: InstallationPublic | null; repos: RepositoryPublic[] };

/**
 * Repositories grouped by the GitHub connection they belong to, so it is
 * clear which account grants access to which repository (and what happens to
 * them when that account is suspended or uninstalled). Repositories that
 * belong to no connection (created from a pasted diff) are listed last.
 */
export function groupRepositories(
  repos: RepositoryPublic[],
  installations: InstallationPublic[],
): Group[] {
  const groups: Group[] = installations.map((installation) => ({
    key: installation.id,
    installation,
    repos: repos.filter((r) => r.installation_id === installation.id),
  }));
  const known = new Set(installations.map((i) => i.id));
  const unattached = repos.filter((r) => !r.installation_id || !known.has(r.installation_id));
  if (unattached.length > 0) groups.push({ key: "none", installation: null, repos: unattached });
  return groups.filter((g) => g.repos.length > 0);
}

export function RepositoryGroups() {
  const repos = useRepositories();
  const installations = useQuery({ queryKey: ["installations"], queryFn: listInstallations });

  if (repos.isPending || installations.isPending) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }
  const error = repos.error ?? installations.error;
  if (error) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {formatApiError(error)}
      </p>
    );
  }

  const groups = groupRepositories(repos.data ?? [], installations.data ?? []);
  if (groups.length === 0) {
    return (
      <Card>
        <CardContent className="flex flex-col items-start gap-3">
          <p className="text-sm text-muted-foreground">
            No GitHub repositories connected yet. Connect a GitHub account to review pull requests with one
            click, no diff pasting needed.
          </p>
          <Button render={<Link href="/github" />}>
            <Plug />
            Connect GitHub
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {groups.map((group) => (
        <Card key={group.key}>
          <CardHeader>
            <CardTitle className="flex flex-wrap items-center gap-2">
              {group.installation ? (
                <>
                  <span>{group.installation.account_login}</span>
                  <Badge variant="secondary">{group.installation.account_type}</Badge>
                  {group.installation.status && group.installation.status !== "active" && (
                    <Badge variant={group.installation.status === "uninstalled" ? "destructive" : "secondary"}>
                      {CONNECTION_STATUS_LABEL[group.installation.status]}
                    </Badge>
                  )}
                </>
              ) : (
                <span>Not connected to GitHub</span>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="divide-y">
              {group.repos.map((repo) => (
                <RepositoryRow key={repo.id} repo={repo} compact={false} />
              ))}
            </ul>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
