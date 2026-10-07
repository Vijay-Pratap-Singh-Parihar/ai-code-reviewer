"use client";

import { useAuth } from "@/components/auth-provider";
import { AuditLog } from "@/components/audit-log";
import { Card, CardContent } from "@/components/ui/card";
import { isOrgAdmin } from "@/lib/permissions";

export default function AuditPage() {
  const { user } = useAuth();

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-lg font-semibold">Audit log</h1>
        <p className="text-sm text-muted-foreground">
          Every sign-in, connection change, review request and data deletion in your organization, newest
          first. Entries are kept after the data they describe is deleted.
        </p>
      </div>
      <Card>
        <CardContent>
          {isOrgAdmin(user) ? (
            <AuditLog />
          ) : (
            <p className="text-sm text-muted-foreground">Only organization owners and admins can view the audit log.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
