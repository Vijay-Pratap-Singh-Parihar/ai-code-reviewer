"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { formatApiError, linkInstallation } from "@/lib/api-client";

// GitHub's OAuth `code` is single-use. React's dev-mode StrictMode mounts
// effects twice, and a second POST with a spent code would fail and replace
// the success with an error — so remember which codes were already sent.
const sentCodes = new Set<string>();

/**
 * Landing point of the GitHub App's callback URL
 * (`/github/setup?installation_id=…&code=…&setup_action=install`). Hands both
 * values to the API, which verifies with GitHub that the signed-in user can
 * really access that installation before linking it — the id alone proves
 * nothing, since anyone can type one into a URL.
 */
export function GitHubSetupHandler({
  installationId,
  code,
  setupAction,
}: {
  installationId: string | null;
  code: string | null;
  setupAction: string | null;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const started = useRef(false);
  const mutation = useMutation({
    mutationFn: linkInstallation,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["installations"] });
      void queryClient.invalidateQueries({ queryKey: ["repositories"] });
      router.replace("/repositories");
    },
  });

  const parsedId = installationId !== null && /^\d+$/.test(installationId) ? Number(installationId) : null;
  const canLink = parsedId !== null && code !== null && code !== "";

  useEffect(() => {
    if (parsedId === null || !code || started.current || sentCodes.has(code)) return;
    started.current = true;
    sentCodes.add(code);
    mutation.mutate({ installation_id: parsedId, code });
  }, [code, parsedId, mutation]);

  if (setupAction === "request") {
    return (
      <Message>
        The installation was requested and is waiting for an organization owner to approve it on GitHub.
        Once approved, come back and click <strong>Connect GitHub</strong> again.
      </Message>
    );
  }

  if (!canLink) {
    return (
      <Message error>
        GitHub didn&apos;t send the expected <code className="font-mono">installation_id</code> and{" "}
        <code className="font-mono">code</code>. Make sure the GitHub App has &ldquo;Request user
        authorization (OAuth) during installation&rdquo; enabled and its Callback URL points to this page.
      </Message>
    );
  }

  if (mutation.isError) {
    return (
      <Message error>
        Couldn&apos;t connect this installation: {formatApiError(mutation.error)}.{" "}
        <Link href="/github" className="underline underline-offset-4">
          Back to GitHub settings
        </Link>
      </Message>
    );
  }

  return <Message>{mutation.isSuccess ? "Connected. Loading your repositories…" : "Connecting your GitHub account…"}</Message>;
}

function Message({ children, error = false }: { children: React.ReactNode; error?: boolean }) {
  return (
    <p role={error ? "alert" : "status"} className={error ? "text-sm text-destructive" : "text-sm text-muted-foreground"}>
      {children}
    </p>
  );
}
