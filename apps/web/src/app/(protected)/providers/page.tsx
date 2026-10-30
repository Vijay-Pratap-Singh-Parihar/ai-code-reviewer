"use client";

import { AiProviders } from "@/components/providers/ai-providers";

export default function ProvidersPage() {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-lg font-semibold">AI Providers</h1>
        <p className="text-sm text-muted-foreground">
          The models that review your pull requests: hosted APIs, or your own model on any OpenAI-compatible
          server. Keys are stored encrypted and never shown again.
        </p>
      </div>
      <AiProviders />
    </div>
  );
}
