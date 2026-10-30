"use client";

import { useQuery } from "@tanstack/react-query";
import { Pencil, Plus } from "lucide-react";
import {
  deleteProvider,
  formatApiError,
  listModelRoutes,
  listProviderKinds,
  listProviders,
  type ProviderPublic,
} from "@/lib/api-client";
import { formatDate } from "@/lib/lifecycle";
import { isOrgAdmin } from "@/lib/permissions";
import { useAuth } from "@/components/auth-provider";
import { DeleteDataDialog } from "@/components/delete-data-dialog";
import { ModelRoutesCard } from "@/components/providers/model-routes-card";
import { ProviderFormDialog } from "@/components/providers/provider-form-dialog";
import { ProviderTestDialog } from "@/components/providers/provider-test-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function CapabilityBadges({ provider }: { provider: ProviderPublic }) {
  const { supports_json: json, supports_tools: tools } = provider.settings;
  if (json === undefined || json === null) {
    return <Badge variant="outline">not tested</Badge>;
  }
  return (
    <>
      <Badge variant={json ? "secondary" : "destructive"}>{json ? "JSON" : "no JSON"}</Badge>
      <Badge variant={tools ? "secondary" : "outline"}>{tools ? "tool calling" : "no tool calling"}</Badge>
    </>
  );
}

function ProviderRow({
  provider,
  canEdit,
  kinds,
}: {
  provider: ProviderPublic;
  canEdit: boolean;
  kinds: Parameters<typeof ProviderFormDialog>[0]["kinds"];
}) {
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 py-3">
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{provider.name}</span>
          <Badge variant="outline">{provider.kind_label}</Badge>
          <CapabilityBadges provider={provider} />
          {provider.used_by.map((tier) => (
            <Badge key={tier}>{tier}</Badge>
          ))}
        </div>
        <span className="truncate font-mono text-xs text-muted-foreground">
          {provider.effective_base_url ?? "default endpoint"}
          {" · "}
          {provider.has_api_key ? `key ${provider.key_hint}` : "no key"}
        </span>
        <span className="text-xs text-muted-foreground">
          {provider.verified_at ? `Last test passed ${formatDate(provider.verified_at)}` : "Never tested successfully"}
        </span>
        {provider.last_test_error && (
          <span role="alert" className="break-all text-xs text-destructive">
            Last test failed: {provider.last_test_error}
          </span>
        )}
      </div>
      {canEdit && (
        <div className="flex flex-wrap gap-2">
          <ProviderTestDialog provider={provider} />
          <ProviderFormDialog
            kinds={kinds}
            provider={provider}
            trigger={
              <Button variant="outline" size="sm">
                <Pencil />
                Edit
              </Button>
            }
          />
          <DeleteDataDialog
            name={provider.name}
            title={`Delete the provider ${provider.name}?`}
            triggerLabel="Delete"
            description={
              <>
                Removes this provider and its encrypted key.
                {provider.used_by.length > 0 && (
                  <>
                    {" "}
                    It is used for <strong>{provider.used_by.join(", ")}</strong>; those steps will have no
                    model until you choose another, and reviews are refused while the review step has none.
                  </>
                )}{" "}
                Past reviews keep their record of which model wrote them.
              </>
            }
            onDelete={() => deleteProvider(provider.id)}
          />
        </div>
      )}
    </li>
  );
}

/**
 * The AI Providers screen: the organisation's LLM providers (hosted APIs or
 * its own OpenAI-compatible endpoint) and which model each review step uses.
 * Keys are entered here only; there is no environment-variable fallback.
 */
export function AiProviders() {
  const { user } = useAuth();
  const canEdit = isOrgAdmin(user);
  const kinds = useQuery({ queryKey: ["provider-kinds"], queryFn: listProviderKinds });
  const providers = useQuery({ queryKey: ["providers"], queryFn: listProviders });
  const routes = useQuery({ queryKey: ["model-routes"], queryFn: listModelRoutes });

  if (kinds.isPending || providers.isPending || routes.isPending) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }
  const error = kinds.error ?? providers.error ?? routes.error;
  if (error) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {formatApiError(error)}
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <ModelRoutesCard
        key={JSON.stringify(routes.data)}
        routes={routes.data ?? []}
        providers={providers.data ?? []}
        canEdit={canEdit}
      />

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Providers</CardTitle>
          {canEdit && (
            <ProviderFormDialog
              kinds={kinds.data ?? []}
              trigger={
                <Button size="sm">
                  <Plus />
                  Add provider
                </Button>
              }
            />
          )}
        </CardHeader>
        <CardContent>
          {(providers.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No providers yet. Add Anthropic, OpenAI or Groq with an API key, or your own model through any
              OpenAI-compatible endpoint (for example Ollama running on this machine).
            </p>
          ) : (
            <ul className="divide-y">
              {(providers.data ?? []).map((provider) => (
                <ProviderRow key={provider.id} provider={provider} canEdit={canEdit} kinds={kinds.data ?? []} />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
