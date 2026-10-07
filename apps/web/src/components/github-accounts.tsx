"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Plug, RefreshCw, ShieldAlert } from "lucide-react";
import {
  deleteInstallationData,
  formatApiError,
  getGitHubApp,
  listInstallations,
  syncInstallation,
  type InstallationPublic,
} from "@/lib/api-client";
import { CONNECTION_STATUS_LABEL, formatDate } from "@/lib/lifecycle";
import { isOrgAdmin, isPlatformAdmin } from "@/lib/permissions";
import { useAuth } from "@/components/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CreateGitHubApp } from "@/components/create-github-app";
import { DeleteDataDialog } from "@/components/delete-data-dialog";

function manageUrl(installation: InstallationPublic): string {
  return installation.account_type === "Organization"
    ? `https://github.com/organizations/${installation.account_login}/settings/installations/${installation.installation_id}`
    : `https://github.com/settings/installations/${installation.installation_id}`;
}

function StatusBadge({ installation }: { installation: InstallationPublic }) {
  const status = installation.status ?? "active";
  const variant = status === "active" ? "outline" : status === "suspended" ? "secondary" : "destructive";
  return <Badge variant={variant}>{CONNECTION_STATUS_LABEL[status]}</Badge>;
}

function InstallationRow({
  installation,
  canDelete,
}: {
  installation: InstallationPublic;
  canDelete: boolean;
}) {
  const queryClient = useQueryClient();
  const sync = useMutation({
    mutationFn: () => syncInstallation(installation.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["installations"] });
      void queryClient.invalidateQueries({ queryKey: ["repositories"] });
    },
  });
  const status = installation.status ?? "active";
  const disconnected = installation.disconnected_repository_count ?? 0;

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="flex flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <span className="font-medium">{installation.account_login}</span>
          <Badge variant="secondary">{installation.account_type}</Badge>
          <StatusBadge installation={installation} />
        </div>
        <span className="text-xs text-muted-foreground">
          {installation.repository_count} active{" "}
          {installation.repository_count === 1 ? "repository" : "repositories"}
          {" · "}connected {formatDate(installation.installed_at)}
          {installation.uninstalled_at && <> · uninstalled {formatDate(installation.uninstalled_at)}</>}
        </span>
        {status === "suspended" && (
          <span className="text-xs text-muted-foreground">
            Suspended on GitHub: reviews are paused and nothing is deleted.
          </span>
        )}
        {disconnected > 0 && installation.purge_after && (
          <span className="text-xs text-amber-700 dark:text-amber-400">
            {disconnected} disconnected {disconnected === 1 ? "repository" : "repositories"}: data is
            deleted automatically from {formatDate(installation.purge_after)}.
          </span>
        )}
        {sync.isError && (
          <span role="alert" className="text-xs text-destructive">
            {formatApiError(sync.error)}
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        {status !== "uninstalled" && (
          <Button variant="outline" size="sm" disabled={sync.isPending} onClick={() => sync.mutate()}>
            <RefreshCw className={sync.isPending ? "animate-spin" : undefined} />
            {sync.isPending ? "Syncing…" : "Sync repos"}
          </Button>
        )}
        {status === "uninstalled" && canDelete && (
          <DeleteDataDialog
            name={installation.account_login}
            description={
              <>
                Permanently deletes this connection and every repository under it: pull request reviews,
                findings, branch indexes and the cloned code. The audit log keeps a record that it was
                deleted. This cannot be undone.
              </>
            }
            onDelete={() => deleteInstallationData(installation.id)}
          />
        )}
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

/** Shown instead of "Create GitHub App" to anyone who isn't a platform admin. */
function AppNotSetUp({ error }: { error?: string | null }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>GitHub isn&apos;t set up yet</CardTitle>
      </CardHeader>
      <CardContent className="flex items-start gap-3 text-sm text-muted-foreground">
        <ShieldAlert className="mt-0.5 size-4 shrink-0" />
        <p>
          revu connects to GitHub through one GitHub App shared by every organization on this server.
          Only a platform administrator can create it. Ask yours to set it up, then come back here to
          connect your GitHub account.
          {error && <span className="mt-2 block text-destructive">The stored GitHub App can&apos;t be used: {error}</span>}
        </p>
      </CardContent>
    </Card>
  );
}

export function GitHubAccounts() {
  const { user } = useAuth();
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
    return isPlatformAdmin(user) ? (
      <CreateGitHubApp error={app.data.error} />
    ) : (
      <AppNotSetUp error={app.data.error} />
    );
  }

  const rows = installations.data ?? [];
  const canDelete = isOrgAdmin(user);

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
        <CardContent className="flex flex-col gap-3">
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
                <InstallationRow key={installation.id} installation={installation} canDelete={canDelete} />
              ))}
            </ul>
          )}
          {rows.some((i) => i.status === "uninstalled") && (
            <p className="text-xs text-muted-foreground">
              When the App is uninstalled, revu keeps that account&apos;s data for a retention period so
              reinstalling restores it, then deletes it automatically. Owners and admins can delete it
              sooner.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
