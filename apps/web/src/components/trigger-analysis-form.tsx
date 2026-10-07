"use client";

import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { triggerAnalysis, formatApiError } from "@/lib/api-client";
import { saveRunMetadata } from "@/lib/run-metadata-store";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

const EXAMPLE_DIFF = `--- a/app.py
+++ b/app.py
@@ -1,3 +1,3 @@
-for i in range(n):
+for i in range(n + 1):
     total += values[i]
`;

export function TriggerAnalysisForm({ onTriggered }: { onTriggered: (runId: string) => void }) {
  const [repoFullName, setRepoFullName] = useState("acme/widgets");
  const [baseBranch, setBaseBranch] = useState("main");
  const [headSha, setHeadSha] = useState("abc1234");
  const [prNumber, setPrNumber] = useState("1");
  const [prTitle, setPrTitle] = useState("Fix off-by-one");
  const [prBody, setPrBody] = useState("");
  const [diff, setDiff] = useState(EXAMPLE_DIFF);

  const mutation = useMutation({
    mutationFn: triggerAnalysis,
    onSuccess: (run) => {
      // The diff isn't persisted server-side (see run-metadata-store's
      // docstring) — stash what we already have in hand so the PR analysis
      // view can render it for this session.
      saveRunMetadata(run.id, {
        repoFullName,
        baseBranch,
        prNumber: Number(prNumber),
        prTitle,
        prBody,
        diff,
        agent: "diff_only",
      });
      onTriggered(run.id);
    },
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    mutation.mutate({
      repo_full_name: repoFullName,
      base_branch: baseBranch,
      head_sha: headSha,
      pr_number: Number(prNumber),
      pr_title: prTitle,
      pr_body: prBody,
      diff,
      agent: "diff_only",
    });
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="repo_full_name">Repository</Label>
          <Input
            id="repo_full_name"
            required
            value={repoFullName}
            onChange={(event) => setRepoFullName(event.target.value)}
            placeholder="acme/widgets"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="base_branch">Base branch</Label>
          <Input id="base_branch" value={baseBranch} onChange={(event) => setBaseBranch(event.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="head_sha">Head SHA</Label>
          <Input
            id="head_sha"
            required
            minLength={7}
            value={headSha}
            onChange={(event) => setHeadSha(event.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pr_number">PR number</Label>
          <Input
            id="pr_number"
            type="number"
            min={1}
            required
            value={prNumber}
            onChange={(event) => setPrNumber(event.target.value)}
          />
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="pr_title">PR title</Label>
        <Input id="pr_title" required value={prTitle} onChange={(event) => setPrTitle(event.target.value)} />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="pr_body">PR body (optional)</Label>
        <Textarea id="pr_body" rows={2} value={prBody} onChange={(event) => setPrBody(event.target.value)} />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="diff">Diff</Label>
        <Textarea
          id="diff"
          required
          rows={8}
          className="font-mono text-xs"
          value={diff}
          onChange={(event) => setDiff(event.target.value)}
        />
      </div>

      <p className="text-xs text-muted-foreground">
        A pasted diff is reviewed on its own (diff-only). For a cross-file review that reads the
        rest of the code, connect the repository through GitHub and review the pull request there.
      </p>

      {mutation.isError && (
        <p role="alert" className="text-sm text-destructive">
          {formatApiError(mutation.error)}
        </p>
      )}
      <Button type="submit" disabled={mutation.isPending}>
        {mutation.isPending ? "Triggering…" : "Trigger review"}
      </Button>
    </form>
  );
}
