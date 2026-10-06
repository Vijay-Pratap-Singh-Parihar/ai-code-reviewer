import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { AppSidebar } from "@/components/app-sidebar";
import { SidebarProvider } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";

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

  it("renders the planned sections as disabled, non-navigating items with a Soon badge", () => {
    pathname = "/dashboard";
    renderSidebar();

    for (const label of ["Repositories", "Branch Memory", "AI Providers", "Usage & Budget", "History"]) {
      const button = screen.getByRole("button", { name: new RegExp(label) });
      // The tooltip wrapper needs the button to still fire hover events, so
      // "disabled" here renders as a data-trigger-disabled marker (styled
      // via pointer-events-none) rather than a real native `disabled`
      // attribute.
      expect(button).toHaveAttribute("data-trigger-disabled");
    }
    expect(screen.getAllByText("Soon")).toHaveLength(5);
  });
});
