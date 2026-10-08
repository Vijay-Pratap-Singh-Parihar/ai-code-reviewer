import type { ConnectionStatus } from "@/lib/api-client";

export function formatDate(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" }) : "";
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export const CONNECTION_STATUS_LABEL: Record<ConnectionStatus, string> = {
  active: "Active",
  suspended: "Suspended",
  uninstalled: "Uninstalled",
};

/** Human labels for `db.audit.AuditAction` values; unknown actions show raw. */
export const AUDIT_ACTION_LABEL: Record<string, string> = {
  "auth.login": "Signed in",
  "auth.login_failed": "Failed sign-in",
  "platform_admin.granted": "Platform admin granted",
  "platform_admin.revoked": "Platform admin revoked",
  "github_app.created": "GitHub App created",
  "installation.linked": "GitHub account connected",
  "installation.synced": "Repositories synced",
  "installation.suspended": "GitHub account suspended",
  "installation.unsuspended": "GitHub account unsuspended",
  "installation.uninstalled": "GitHub App uninstalled",
  "repository.connected": "Repository connected",
  "repository.disconnected": "Repository disconnected",
  "repository.settings_changed": "Repository settings changed",
  "review.requested": "Review requested",
  "index.requested": "Index build requested",
  "data.deletion_requested": "Data deletion requested",
  "data.purged": "Data deleted",
  "ai_provider.created": "AI provider added",
  "ai_provider.updated": "AI provider changed",
  "ai_provider.deleted": "AI provider deleted",
  "ai_provider.tested": "AI provider tested",
  "model_routes.changed": "Review models changed",
};

export function auditActionLabel(action: string): string {
  return AUDIT_ACTION_LABEL[action] ?? action;
}
