"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  FolderGit2,
  Plug,
  Network,
  Cpu,
  Wallet,
  History,
  ScrollText,
} from "lucide-react";
import { useAuth } from "@/components/auth-provider";
import { isOrgAdmin } from "@/lib/permissions";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";

/**
 * Wired-up sections first. The rest of `Product_Architecture_FullStack.md`
 * §6's planned sections are shown disabled with a "Soon" badge rather than
 * omitted entirely — visualizing the real planned product instead of either
 * a dead link (bad) or hiding the roadmap (less honest about what's coming).
 */
const NAV_ITEMS = [
  {
    title: "Dashboard",
    href: "/dashboard",
    icon: LayoutDashboard,
    isActive: (path: string) => path === "/dashboard" || path.startsWith("/runs/"),
  },
  {
    title: "Repositories",
    href: "/repositories",
    icon: FolderGit2,
    isActive: (path: string) => path.startsWith("/repositories"),
  },
  {
    title: "GitHub",
    href: "/github",
    icon: Plug,
    isActive: (path: string) => path.startsWith("/github"),
  },
];

const PLANNED_ITEMS = [
  { title: "Branch Memory", icon: Network },
  { title: "AI Providers", icon: Cpu },
  { title: "Usage & Budget", icon: Wallet },
  { title: "History", icon: History },
];

// Organisation administration: owners and admins only (the API enforces it too).
const ADMIN_ITEMS = [
  {
    title: "Audit log",
    href: "/audit",
    icon: ScrollText,
    isActive: (path: string) => path.startsWith("/audit"),
  },
];

export function AppSidebar() {
  const pathname = usePathname();
  const { user } = useAuth();

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <div className="flex items-center gap-2 px-2 py-1.5">
          <div className="flex size-6 shrink-0 items-center justify-center rounded-md bg-primary text-xs font-bold text-primary-foreground">
            r
          </div>
          <span className="text-sm font-semibold group-data-[collapsible=icon]:hidden">revu</span>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Platform</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {NAV_ITEMS.map((item) => (
                <SidebarMenuItem key={item.href}>
                  <SidebarMenuButton
                    isActive={item.isActive(pathname)}
                    tooltip={item.title}
                    render={<Link href={item.href} />}
                  >
                    <item.icon />
                    <span>{item.title}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {isOrgAdmin(user) && (
          <SidebarGroup>
            <SidebarGroupLabel>Organization</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {ADMIN_ITEMS.map((item) => (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton
                      isActive={item.isActive(pathname)}
                      tooltip={item.title}
                      render={<Link href={item.href} />}
                    >
                      <item.icon />
                      <span>{item.title}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        )}

        <SidebarGroup>
          <SidebarGroupLabel>Planned</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {PLANNED_ITEMS.map((item) => (
                <SidebarMenuItem key={item.title}>
                  <SidebarMenuButton disabled tooltip={`${item.title}: coming in a later stage`}>
                    <item.icon />
                    <span>{item.title}</span>
                  </SidebarMenuButton>
                  <SidebarMenuBadge>Soon</SidebarMenuBadge>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  );
}
