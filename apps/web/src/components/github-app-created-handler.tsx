"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Plug } from "lucide-react";
import { completeAppManifest, formatApiError } from "@/lib/api-client";
import { Button } from "@/components/ui/button";

// The manifest `code` is single-use; see github-setup-handler for why this
// guard exists alongside the ref (React StrictMode double-runs effects).
const sentCodes = new Set<string>();

/**
 * Landing point of the manifest flow's `redirect_url`
 * (`/github/app-created?code=…&state=…`). Finishes creating the App, then
 * offers the next and last step: installing it on repositories.
 */
export function GitHubAppCreatedHandler({ code, state }: { code: string | null; state: string | null }) {
  const queryClient = useQueryClient();
  const started = useRef(false);
  const mutation = useMutation({
    mutationFn: completeAppManifest,
    onSuccess: (info) => queryClient.setQueryData(["github-app"], info),
  });

  useEffect(() => {
    if (!code || !state || started.current || sentCodes.has(code)) return;
    started.current = true;
    sentCodes.add(code);
    mutation.mutate({ code, state });
  }, [code, state, mutation]);

  if (!code || !state) {
    return (
      <p role="alert" className="text-sm text-destructive">
        GitHub didn&apos;t send back the expected <code className="font-mono">code</code> and{" "}
        <code className="font-mono">state</code>.{" "}
        <Link href="/github" className="underline underline-offset-4">
          Start again
        </Link>
      </p>
    );
  }

  if (mutation.isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Couldn&apos;t finish creating the GitHub App: {formatApiError(mutation.error)}.{" "}
        <Link href="/github" className="underline underline-offset-4">
          Back to GitHub settings
        </Link>
      </p>
    );
  }

  if (!mutation.isSuccess) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Finishing GitHub App setup…
      </p>
    );
  }

  const info = mutation.data;
  return (
    <div className="flex flex-col items-start gap-4">
      <p role="status" className="flex items-center gap-2 text-sm">
        <CheckCircle2 className="size-4 text-emerald-600" />
        GitHub App <span className="font-medium">{info.slug}</span> is ready.
      </p>
      <p className="text-sm text-muted-foreground">
        Last step: install it on the repositories you want reviewed. Auto-review stays off for each
        repository until you turn it on.
      </p>
      <Button render={<a href={info.install_url ?? "/github"} />}>
        <Plug />
        Install on your repositories
      </Button>
    </div>
  );
}
