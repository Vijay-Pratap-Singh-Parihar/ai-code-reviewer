import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { AppSidebar } from "@/components/app-sidebar";
import { SidebarProvider } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { setAuthState } from "@/lib/auth-store";

let pathname = "/dashboard";

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
}));

function renderSidebar() {
  return render(
    <TooltipProvider>
      <SidebarProvider>
        <AppSidebar />
      </SidebarProvider>
    </TooltipProvider>,
  );
}

describe("AppSidebar", () => {
  it("renders Dashboard as a real link to /dashboard", () => {
    pathname = "/dashboard";
    renderSidebar();

    const link = screen.getByRole("link", { name: /dashboard/i });
    expect(link).toHaveAttribute("href", "/dashboard");
  });

  it("marks Dashboard active on a run detail page too", () => {
    pathname = "/runs/abc-123";
    renderSidebar();

    const link = screen.getByRole("link", { name: /dashboard/i });
    // Base UI renders a true boolean state as a bare, valueless data
    // attribute (`data-active=""`), not the string "true".
    expect(link).toHaveAttribute("data-active");
  });

  it("renders Repositories and GitHub as real links, active on their sub-pages", () => {
    pathname = "/repositories/repo-1";
    renderSidebar();

    const repos = screen.getByRole("link", { name: /repositories/i });
    expect(repos).toHaveAttribute("href", "/repositories");
    expect(repos).toHaveAttribute("data-active");
    const github = screen.getByRole("link", { name: /github/i });
    expect(github).toHaveAttribute("href", "/github");
    expect(github).not.toHaveAttribute("data-active");
    expect(screen.getByRole("link", { name: /dashboard/i })).not.toHaveAttribute("data-active");
  });

  it("links every screen, with nothing left as coming soon", () => {
    pathname = "/history";
    renderSidebar();

    expect(screen.getByRole("link", { name: /ai providers/i })).toHaveAttribute("href", "/providers");
    expect(screen.getByRole("link", { name: /branch memory/i })).toHaveAttribute("href", "/branch-memory");
    expect(screen.getByRole("link", { name: /usage & budget/i })).toHaveAttribute("href", "/usage");
    const history = screen.getByRole("link", { name: /history/i });
    expect(history).toHaveAttribute("href", "/history");
    expect(history).toHaveAttribute("data-active");
    expect(screen.queryByText("Soon")).not.toBeInTheDocument();
  });

  it("shows the audit log to owners and admins only", () => {
    pathname = "/audit";
    setAuthState({
      accessToken: "t",
      status: "authenticated",
      user: { id: "u", org_id: "o", email: "a@b.c", role: "owner" },
    });
    const { unmount } = renderSidebar();
    expect(screen.getByRole("link", { name: /audit log/i })).toHaveAttribute("href", "/audit");
    expect(screen.getByRole("link", { name: /audit log/i })).toHaveAttribute("data-active");
    unmount();

    setAuthState({
      accessToken: "t",
      status: "authenticated",
      user: { id: "u", org_id: "o", email: "a@b.c", role: "member" },
    });
    renderSidebar();
    expect(screen.queryByRole("link", { name: /audit log/i })).not.toBeInTheDocument();
    setAuthState({ accessToken: null, user: null, status: "unauthenticated" });
  });
});
