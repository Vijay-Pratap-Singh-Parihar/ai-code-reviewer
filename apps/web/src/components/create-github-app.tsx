"use client";

import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { formatApiError, startAppManifest } from "@/lib/api-client";
import { submitManifestForm } from "@/lib/github-manifest";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * One-click setup of this server's GitHub App. revu prepares the App's
 * settings (read-only permissions, PR/push events, callback and webhook
 * URLs); GitHub shows them for confirmation and sends the credentials back.
 * Nothing is typed by hand except an optional organization to own it.
 */
export function CreateGitHubApp({ error }: { error?: string | null }) {
  const [organization, setOrganization] = useState("");
  const start = useMutation({
    mutationFn: () => startAppManifest(organization.trim() || undefined),
    onSuccess: ({ action_url, manifest }) => submitManifestForm(action_url, manifest),
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    start.mutate();
  }

  // Stays "pending" through the navigation to GitHub.
  const busy = start.isPending || start.isSuccess;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Set up GitHub</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        <p className="text-muted-foreground">
          revu connects to GitHub through a GitHub App. Create one in a click: GitHub shows you the
          settings revu prepared (read-only access to code and pull requests), you confirm, and you&apos;re
          brought back here to install it on your repositories.
        </p>
        {error && (
          <p role="alert" className="text-destructive">
            The stored GitHub App can&apos;t be used: {error}
          </p>
        )}
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <div className="flex max-w-sm flex-col gap-1.5">
            <Label htmlFor="gh-org">Organization (optional)</Label>
            <Input
              id="gh-org"
              value={organization}
              onChange={(event) => setOrganization(event.target.value)}
              placeholder="Leave empty to use your personal account"
            />
          </div>
          <div>
            <Button type="submit" disabled={busy}>
              <Sparkles />
              {busy ? "Opening GitHub…" : "Create GitHub App"}
            </Button>
          </div>
          {start.isError && (
            <p role="alert" className="text-destructive">
              {formatApiError(start.error)}
            </p>
          )}
        </form>
      </CardContent>
    </Card>
  );
}
