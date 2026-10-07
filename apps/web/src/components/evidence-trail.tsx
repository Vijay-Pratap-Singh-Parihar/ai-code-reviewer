import type { FindingPublic } from "@/lib/api-client";
import { SEVERITY_BADGE } from "@/lib/badges";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

/**
 * One finding plus the real locations the agent cites as evidence for it —
 * which can be files outside the diff entirely (that's the whole point of
 * `cross_file`'s tool calls), so this is rendered independently of the diff
 * viewer rather than only as inline widgets on diff lines.
 */
export function EvidenceTrail({ findings }: { findings: FindingPublic[] }) {
  if (findings.length === 0) {
    return <p className="text-sm text-muted-foreground">No findings.</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      {findings.map((finding, index) => {
        const severityBadge = SEVERITY_BADGE[finding.severity] ?? SEVERITY_BADGE.low;
        return (
          <Card key={index}>
            <CardContent className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={severityBadge.variant} className={severityBadge.className}>
                  {finding.severity}
                </Badge>
                <span className="font-mono text-xs text-muted-foreground">
                  {finding.file_path}:{finding.line_start}-{finding.line_end}
                </span>
                <span className="text-xs text-muted-foreground">
                  confidence {finding.confidence.toFixed(2)} &middot; {finding.agent_name}
                </span>
              </div>
              <p className="text-sm">{finding.message}</p>

              {finding.evidence.length > 0 && (
                <div className="mt-1 flex flex-col gap-1.5 border-t pt-2">
                  <span className="text-xs font-medium text-muted-foreground">
                    Evidence trail (files consulted and why):
                  </span>
                  <ul className="flex flex-col gap-1">
                    {finding.evidence.map((item, evidenceIndex) => (
                      <li key={evidenceIndex} className="text-xs">
                        <span className="font-mono text-muted-foreground">
                          {item.file_path}:{item.line_start}-{item.line_end}
                        </span>{" "}
                        · {item.reason}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
