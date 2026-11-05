"use client";

import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { formatApiError, updateRepository, type RepositoryPublic, type ReviewSettings } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

const DEFAULTS: ReviewSettings = {
  ignore_globs: [],
  min_severity: "low",
  max_comments: null,
  max_tokens_per_review: null,
};

const SEVERITY_LABEL: Record<ReviewSettings["min_severity"], string> = {
  low: "Show everything",
  medium: "Medium and above",
  high: "High and critical",
  critical: "Critical only",
};

function optionalNumber(value: string): number | null {
  return value.trim() === "" ? null : Number(value);
}

/**
 * A repository's review settings (owners and admins): files to leave out,
 * the lowest severity worth showing, a cap on findings per review, and a
 * token limit per review. Above the limit a review stops before spending
 * anything and offers "Run anyway" on its page.
 */
export function ReviewSettingsForm({ repo, canEdit }: { repo: RepositoryPublic; canEdit: boolean }) {
  const queryClient = useQueryClient();
  const current = repo.review_settings ?? DEFAULTS;
  const [globs, setGlobs] = useState(current.ignore_globs.join("\n"));
  const [severity, setSeverity] = useState<ReviewSettings["min_severity"]>(current.min_severity);
  const [maxComments, setMaxComments] = useState(current.max_comments?.toString() ?? "");
  const [maxTokens, setMaxTokens] = useState(current.max_tokens_per_review?.toString() ?? "");
  const [saved, setSaved] = useState(false);

  const save = useMutation({
    mutationFn: (review_settings: ReviewSettings) => updateRepository(repo.id, { review_settings }),
    onSuccess: (updated) => {
      setSaved(true);
      queryClient.setQueryData<RepositoryPublic[]>(["repositories"], (repos) =>
        repos?.map((r) => (r.id === updated.id ? updated : r)),
      );
    },
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSaved(false);
    save.mutate({
      ignore_globs: globs
        .split("\n")
        .map((g) => g.trim())
        .filter(Boolean),
      min_severity: severity,
      max_comments: optionalNumber(maxComments),
      max_tokens_per_review: optionalNumber(maxTokens),
    });
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <fieldset disabled={!canEdit} className="grid gap-4 md:grid-cols-2">
        <div className="flex flex-col gap-1.5 md:row-span-2">
          <Label htmlFor="ignore-globs">Ignore these files</Label>
          <Textarea
            id="ignore-globs"
            rows={5}
            placeholder={"docs/**\n*.snap\nmigrations/**"}
            value={globs}
            onChange={(e) => setGlobs(e.target.value)}
            className="font-mono text-xs"
          />
          <span className="text-xs text-muted-foreground">
            One pattern per line. Lockfiles and generated files are always left out.
          </span>
        </div>
        <div className="flex flex-col gap-1.5">
          <span id="min-severity-label" className="text-sm font-medium">
            Findings to show
          </span>
          <Select value={severity} onValueChange={(v) => setSeverity((v ?? "low") as ReviewSettings["min_severity"])}>
            <SelectTrigger aria-labelledby="min-severity-label" className="w-full">
              <SelectValue>{(v: ReviewSettings["min_severity"]) => SEVERITY_LABEL[v]}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(SEVERITY_LABEL) as ReviewSettings["min_severity"][]).map((key) => (
                <SelectItem key={key} value={key}>
                  {SEVERITY_LABEL[key]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="max-comments">Max findings per review</Label>
            <Input
              id="max-comments"
              type="number"
              min={1}
              max={100}
              placeholder="No limit"
              value={maxComments}
              onChange={(e) => setMaxComments(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="max-tokens">Token limit per review</Label>
            <Input
              id="max-tokens"
              type="number"
              min={1000}
              step={1000}
              placeholder="No limit"
              value={maxTokens}
              onChange={(e) => setMaxTokens(e.target.value)}
            />
          </div>
        </div>
      </fieldset>
      <p className="text-xs text-muted-foreground">
        A review estimated above the token limit stops before anything is sent to the model and can be run anyway from
        its page.
      </p>
      {canEdit ? (
        <div className="flex items-center gap-3">
          <Button type="submit" size="sm" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save settings"}
          </Button>
          {saved && !save.isPending && <span className="text-sm text-muted-foreground">Saved.</span>}
          {save.isError && (
            <span role="alert" className="text-sm text-destructive">
              {formatApiError(save.error)}
            </span>
          )}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">Organization owners and admins can change these settings.</p>
      )}
    </form>
  );
}
