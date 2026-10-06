"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  GitBranch,
  Network,
  Cpu,
  Wallet,
  History,
} from "lucide-react";
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
 * Only Dashboard is wired up today. The rest of `Product_Architecture_FullStack.md`
 * §6's planned sections are shown disabled with a "Soon" badge rather than
 * omitted entirely — visualizing the real planned product instead of either
 * a dead link (bad) or hiding the roadmap (less honest about what's coming).
 */
const PLANNED_ITEMS = [
  { title: "Repositories", icon: GitBranch },
  { title: "Branch Memory", icon: Network },
  { title: "AI Providers", icon: Cpu },
  { title: "Usage & Budget", icon: Wallet },
  { title: "History", icon: History },
];

export function AppSidebar() {
  const pathname = usePathname();
  const dashboardActive = pathname === "/dashboard" || pathname.startsWith("/runs/");

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
              <SidebarMenuItem>
                <SidebarMenuButton isActive={dashboardActive} tooltip="Dashboard" render={<Link href="/dashboard" />}>
                  <LayoutDashboard />
                  <span>Dashboard</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarGroup>
          <SidebarGroupLabel>Planned</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {PLANNED_ITEMS.map((item) => (
                <SidebarMenuItem key={item.title}>
                  <SidebarMenuButton disabled tooltip={`${item.title} — coming in a later stage`}>
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
