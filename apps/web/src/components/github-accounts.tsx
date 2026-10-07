"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Plug, RefreshCw } from "lucide-react";
import {
  formatApiError,
  getGitHubApp,
  listInstallations,
  syncInstallation,
  type InstallationPublic,
} from "@/lib/api-client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CreateGitHubApp } from "@/components/create-github-app";

function manageUrl(installation: InstallationPublic): string {
  return installation.account_type === "Organization"
    ? `https://github.com/organizations/${installation.account_login}/settings/installations/${installation.installation_id}`
    : `https://github.com/settings/installations/${installation.installation_id}`;
}

function InstallationRow({ installation }: { installation: InstallationPublic }) {
  const queryClient = useQueryClient();
  const sync = useMutation({
    mutationFn: () => syncInstallation(installation.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["installations"] });
      void queryClient.invalidateQueries({ queryKey: ["repositories"] });
    },
  });

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="flex flex-col">
        <div className="flex items-center gap-2">
          <span className="font-medium">{installation.account_login}</span>
          <Badge variant="secondary">{installation.account_type}</Badge>
        </div>
        <span className="text-xs text-muted-foreground">
          {installation.repository_count} {installation.repository_count === 1 ? "repository" : "repositories"}
          {" · "}connected {new Date(installation.installed_at).toLocaleDateString()}
        </span>
        {sync.isError && (
          <span role="alert" className="text-xs text-destructive">
            {formatApiError(sync.error)}
          </span>
        )}
      </div>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" disabled={sync.isPending} onClick={() => sync.mutate()}>
          <RefreshCw className={sync.isPending ? "animate-spin" : undefined} />
          {sync.isPending ? "Syncing…" : "Sync repos"}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          render={<a href={manageUrl(installation)} target="_blank" rel="noreferrer" />}
        >
          Manage on GitHub
          <ExternalLink />
        </Button>
      </div>
    </li>
  );
}

export function GitHubAccounts() {
  const app = useQuery({ queryKey: ["github-app"], queryFn: getGitHubApp });
  const installations = useQuery({ queryKey: ["installations"], queryFn: listInstallations });

  if (app.isPending) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }
  if (app.isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {formatApiError(app.error)}
      </p>
    );
  }

  if (!app.data.configured) {
    return <CreateGitHubApp error={app.data.error} />;
  }

  const rows = installations.data ?? [];

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Connect a GitHub account</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 text-sm">
          <p className="text-muted-foreground">
            Install the revu GitHub App on a personal account or organization and choose which repositories
            it can read. You&apos;ll be brought back here when it&apos;s done.
          </p>
          <div>
            <Button render={<a href={app.data.install_url ?? "#"} />}>
              <Plug />
              Connect GitHub
            </Button>
          </div>
          {app.data.slug && (
            <p className="text-xs text-muted-foreground">
              Using the GitHub App{" "}
              <a href={app.data.app_url ?? "#"} target="_blank" rel="noreferrer" className="underline underline-offset-4">
                {app.data.slug}
              </a>
              {app.data.source === "env" ? " (configured by environment variables)" : ""}
              {app.data.webhook_proxy_url ? " · webhooks relayed through smee.io" : ""}.
            </p>
          )}
          {!app.data.webhook_configured && (
            <p className="text-xs text-amber-700 dark:text-amber-400">
              No webhook secret is configured, so GitHub events (auto-review, index refresh on push) are
              rejected. Manual reviews still work.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Connected accounts</CardTitle>
        </CardHeader>
        <CardContent>
          {installations.isPending ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : installations.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {formatApiError(installations.error)}
            </p>
          ) : rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">No GitHub accounts connected yet.</p>
          ) : (
            <ul className="divide-y">
              {rows.map((installation) => (
                <InstallationRow key={installation.id} installation={installation} />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
