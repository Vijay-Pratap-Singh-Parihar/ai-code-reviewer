"use client";

import { useMemo } from "react";
import { Diff, Hunk, getChangeKey, parseDiff, type ChangeData } from "react-diff-view";
import "react-diff-view/style/index.css";
import type { FindingPublic } from "@/lib/api-client";
import { SEVERITY_BADGE } from "@/lib/badges";
import { Badge } from "@/components/ui/badge";
import { ensureGitDiffHeaders } from "@/lib/diff-utils";

/** Strips a git-style `a/`/`b/` prefix so it matches `Finding.file_path`,
 * which never carries one (it's always a plain repo-relative path). */
function stripPrefix(path: string): string {
  return path.replace(/^[ab]\//, "");
}

/** The new-file line number a change lands on, or `null` for a pure
 * deletion — there's no new-file line to anchor a finding to. */
function newLineNumberOf(change: ChangeData): number | null {
  if (change.type === "delete") return null;
  return change.type === "insert" ? change.lineNumber : change.newLineNumber;
}

function FindingWidget({ finding }: { finding: FindingPublic }) {
  const badge = SEVERITY_BADGE[finding.severity] ?? SEVERITY_BADGE.low;
  return (
    <div className="border-l-2 border-l-primary bg-muted/50 px-3 py-2 text-sm">
      <div className="flex items-center gap-2">
        <Badge variant={badge.variant} className={badge.className}>
          {finding.severity}
        </Badge>
        <span className="text-xs text-muted-foreground">
          {finding.category} &middot; confidence {finding.confidence.toFixed(2)} &middot; {finding.agent_name}
        </span>
      </div>
      <p className="mt-1">{finding.message}</p>
    </div>
  );
}

/**
 * Renders a unified diff with findings anchored inline at the lines they
 * cite — the "split diff with findings shown inline" half of the PR
 * analysis view (the evidence trail itself is rendered separately, since
 * evidence can point at files that aren't part of this diff at all).
 */
export function DiffViewer({ diffText, findings }: { diffText: string; findings: FindingPublic[] }) {
  const files = useMemo(() => {
    try {
      return parseDiff(ensureGitDiffHeaders(diffText));
    } catch {
      return [];
    }
  }, [diffText]);

  // `parseDiff` doesn't throw on garbage input — it can return a file entry
  // with no hunks at all, which isn't useful to render as a diff.
  const hasRenderableContent = files.some((file) => file.hunks.length > 0);
  if (!hasRenderableContent) {
    return <p className="text-sm text-muted-foreground">Couldn&apos;t parse this diff.</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      {files.map((file) => {
        const filePath = stripPrefix(file.newPath !== "/dev/null" ? file.newPath : file.oldPath);
        const fileFindings = findings.filter((f) => stripPrefix(f.file_path) === filePath);

        // Anchor each finding to the last matching line in its range, so it
        // reads as "the comment follows the flagged block" (GitHub's
        // convention for inline review comments).
        const widgets: Record<string, React.ReactNode> = {};
        for (const hunk of file.hunks) {
          for (const change of hunk.changes) {
            const lineNumber = newLineNumberOf(change);
            if (lineNumber === null) continue;
            const matches = fileFindings.filter(
              (f) => lineNumber >= f.line_start && lineNumber <= f.line_end,
            );
            if (matches.length === 0) continue;
            const isLastMatchingLine = !hunk.changes.some((other) => {
              const otherLine = newLineNumberOf(other);
              return (
                otherLine !== null &&
                otherLine > lineNumber &&
                matches.some((f) => otherLine >= f.line_start && otherLine <= f.line_end)
              );
            });
            if (!isLastMatchingLine) continue;
            widgets[getChangeKey(change)] = (
              <div className="flex flex-col gap-1">
                {matches.map((finding, index) => (
                  <FindingWidget key={index} finding={finding} />
                ))}
              </div>
            );
          }
        }

        return (
          <div key={`${file.oldPath}-${file.newPath}`} className="overflow-hidden rounded-lg border">
            <div className="border-b bg-muted/50 px-3 py-1.5 font-mono text-xs text-muted-foreground">
              {filePath}
            </div>
            <Diff viewType="unified" diffType={file.type} hunks={file.hunks} widgets={widgets}>
              {(hunks) => hunks.map((hunk) => <Hunk key={hunk.content} hunk={hunk} />)}
            </Diff>
          </div>
        );
      })}
    </div>
  );
}
