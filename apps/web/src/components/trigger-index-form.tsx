"use client";

import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { triggerIndex, formatApiError } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * Builds/refreshes a branch index — the prerequisite `agent="cross_file"`
 * needs (POST /repos/index) but there's no on-demand indexing fallback for.
 * Deliberately a separate step from the review trigger below rather than a
 * combined "index then review" flow: it matches today's API surface exactly
 * (two endpoints, two triggers) without adding orchestration logic for a
 * build that can take longer than an HTTP request should wait around for.
 */
export function TriggerIndexForm() {
  const [repoFullName, setRepoFullName] = useState("acme/widgets");
  const [branchName, setBranchName] = useState("main");
  const [repoPath, setRepoPath] = useState("/tmp/acme-widgets");

  const mutation = useMutation({ mutationFn: triggerIndex });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    mutation.mutate({ repo_full_name: repoFullName, branch_name: branchName, repo_path: repoPath });
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="idx_repo_full_name">Repository</Label>
          <Input
            id="idx_repo_full_name"
            required
            value={repoFullName}
            onChange={(event) => setRepoFullName(event.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="idx_branch_name">Branch</Label>
          <Input
            id="idx_branch_name"
            value={branchName}
            onChange={(event) => setBranchName(event.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="idx_repo_path">Repo path (worker filesystem)</Label>
          <Input
            id="idx_repo_path"
            required
            value={repoPath}
            onChange={(event) => setRepoPath(event.target.value)}
          />
        </div>
      </div>
      {mutation.isError && (
        <p role="alert" className="text-sm text-destructive">
          {formatApiError(mutation.error)}
        </p>
      )}
      {mutation.isSuccess && (
        <p className="text-sm text-muted-foreground">
          Build triggered — status <code className="font-mono">{mutation.data.status}</code>.
          It can take a few seconds; retry the review below once it&apos;s ready.
        </p>
      )}
      <Button type="submit" variant="outline" disabled={mutation.isPending} className="self-start">
        {mutation.isPending ? "Triggering…" : "Build branch index"}
      </Button>
    </form>
  );
}
