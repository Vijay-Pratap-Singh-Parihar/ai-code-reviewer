import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DashboardOverview } from "@/components/dashboard-overview";
import { getAdoptionStats, getBranchMemory, getUsage, listHistory, type Usage } from "@/lib/api-client";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return {
    ...actual,
    getUsage: vi.fn(),
    getAdoptionStats: vi.fn(),
    getBranchMemory: vi.fn(),
    listHistory: vi.fn(),
  };
});

describe("DashboardOverview", () => {
  beforeEach(() => {
    vi.mocked(getUsage).mockResolvedValue({
      spent_usd: 1.5,
      tokens: 1000,
      runs: 3,
      budget: { monthly_budget_usd: 10, token_cap: null, warn_percent: 80 },
      used_percent: 15,
      status: "ok",
    } as Usage);
    vi.mocked(getAdoptionStats).mockResolvedValue({
      runs: 3,
      succeeded: 2,
      failed: 1,
      in_progress: 0,
      pull_requests: 2,
      merged_pull_requests: 1,
      posted_to_github: 0,
      findings: 4,
      by_severity: {},
      accepted: 3,
      dismissed: 1,
      ignored: 0,
      no_feedback: 0,
      acceptance_rate: 0.75,
    });
    vi.mocked(getBranchMemory).mockResolvedValue([
      { has_ready_index: true, state: "ready" },
      { has_ready_index: false, state: "failed" },
    ] as never);
    vi.mocked(listHistory).mockResolvedValue({
      runs: [
        {
          id: "r1",
          created_at: "2026-11-05T10:00:00Z",
          status: "succeeded",
          pr_title: "Fix the loop",
          repo_full_name: "acme/widgets",
          pr_number: 7,
          findings: 2,
        },
      ] as never,
      next_before: null,
    });
  });

  it("summarises spend, reviews, adoption, index health and recent reviews", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <DashboardOverview />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("$1.50")).toBeInTheDocument();
    expect(screen.getByText("of $10.00 budget · 15%")).toBeInTheDocument();
    expect(await screen.findByText("75%")).toBeInTheDocument();
    expect(await screen.findByText("1 / 2")).toBeInTheDocument();
    expect(screen.getByText("1 last build failed")).toBeInTheDocument();
    expect((await screen.findByText("Fix the loop")).closest("a")).toHaveAttribute("href", "/runs/r1");
    expect(listHistory).toHaveBeenCalledWith({ limit: 5 });
  });
});
