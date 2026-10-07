"use client";

import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { formatApiError, listAuditEntries, type AuditEntry } from "@/lib/api-client";
import { AUDIT_ACTION_LABEL, auditActionLabel, formatDateTime } from "@/lib/lifecycle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const PAGE_SIZE = 50;
const ALL = "all";

function details(entry: AuditEntry): string {
  return Object.entries(entry.metadata)
    .map(([key, value]) => `${key.replaceAll("_", " ")}: ${String(value)}`)
    .join(" · ");
}

function actionVariant(action: string): "destructive" | "secondary" | "outline" {
  if (action === "data.purged" || action === "auth.login_failed" || action.endsWith("uninstalled")) {
    return "destructive";
  }
  if (action.startsWith("auth.")) return "outline";
  return "secondary";
}

/**
 * The organisation's audit trail, newest first: who did what to which
 * resource, including what the system did on its own (webhooks, the
 * retention job). Entries are written by the API and worker in the same
 * transaction as the change, and they outlive the data they describe.
 */
export function AuditLog() {
  const [action, setAction] = useState<string>(ALL);
  const query = useInfiniteQuery({
    queryKey: ["audit", action],
    queryFn: ({ pageParam }) =>
      listAuditEntries({ limit: PAGE_SIZE, before: pageParam, action: action === ALL ? null : action }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_before,
  });

  const entries = query.data?.pages.flatMap((page) => page.entries) ?? [];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex max-w-xs flex-col gap-1.5">
        <span id="audit-action-label" className="text-sm font-medium">
          Event
        </span>
        <Select value={action} onValueChange={(value) => setAction(String(value ?? ALL))}>
          <SelectTrigger aria-labelledby="audit-action-label" className="w-full">
            <SelectValue>{(value: string) => (value === ALL ? "All events" : auditActionLabel(value))}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All events</SelectItem>
            {Object.entries(AUDIT_ACTION_LABEL).map(([value, label]) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {query.isPending ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : query.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {formatApiError(query.error)}
        </p>
      ) : entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">No events recorded yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>When</TableHead>
              <TableHead>Event</TableHead>
              <TableHead>Target</TableHead>
              <TableHead>By</TableHead>
              <TableHead>Details</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.map((entry) => (
              <TableRow key={entry.id}>
                <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                  {formatDateTime(entry.at)}
                </TableCell>
                <TableCell>
                  <Badge variant={actionVariant(entry.action)}>{auditActionLabel(entry.action)}</Badge>
                </TableCell>
                <TableCell className="font-mono text-xs">{entry.target}</TableCell>
                <TableCell className="text-xs">{entry.actor_email ?? "System"}</TableCell>
                <TableCell className="max-w-xs truncate text-xs text-muted-foreground" title={details(entry)}>
                  {details(entry)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {query.hasNextPage && (
        <div>
          <Button variant="outline" size="sm" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
            {query.isFetchingNextPage ? "Loading…" : "Load older events"}
          </Button>
        </div>
      )}
    </div>
  );
}
