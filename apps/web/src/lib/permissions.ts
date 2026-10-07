import type { UserPublic } from "@/lib/api-client";

/** Organisation owners and admins: repository settings, data deletion, audit log. */
export function isOrgAdmin(user: UserPublic | null | undefined): boolean {
  return user?.role === "owner" || user?.role === "admin";
}

/** The deployment's operator: may create the GitHub App every organisation installs. */
export function isPlatformAdmin(user: UserPublic | null | undefined): boolean {
  return user?.is_platform_admin === true;
}
