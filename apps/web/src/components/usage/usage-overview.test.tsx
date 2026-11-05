import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { UsageOverview } from "@/components/usage/usage-overview";
import { BudgetBanner } from "@/components/usage/budget-status";
import { getUsage, setBudget, type Usage, type UserRole } from "@/lib/api-client";
import { setAuthState } from "@/lib/auth-store";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, getUsage: vi.fn(), setBudget: vi.fn() };
});

function usage(overrides: Partial<Usage> = {}): Usage {
  return {
    period_start: "2026-11-01T00:00:00Z",
    resets_at: "2026-12-01T00:00:00Z",
    spent_usd: 0.75,
    tokens: 1500,
    runs: 2,
    budget: { monthly_budget_usd: 1, token_cap: null, warn_percent: 80 },
    used_percent: 75,
    status: "ok",
    daily: [
      { day: "2026-11-01", cost_usd: 0, tokens: 0, runs: 0 },
      { day: "2026-11-02", cost_usd: 0.75, tokens: 1500, runs: 2 },
    ],
    months: [
      { month: "2026-10", cost_usd: 0, tokens: 0, runs: 0 },
      { month: "2026-11", cost_usd: 0.75, tokens: 1500, runs: 2 },
    ],
    by_repo: [{ repo_full_name: "acme/widgets", cost_usd: 0.75, tokens: 1500, runs: 2 }],
    by_model: [{ provider_name: "Groq", model: "llama-3.3-70b", cost_usd: 0.75, tokens: 1500, runs: 2 }],
    ...overrides,
  };
}

function signIn(role: UserRole) {
  setAuthState({ accessToken: "t", status: "authenticated", user: { id: "u", org_id: "o", email: "a@b.c", role } });
}

function renderWithQuery(ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>,
  );
}

describe("UsageOverview", () => {
  beforeEach(() => {
    vi.mocked(getUsage).mockReset();
    vi.mocked(setBudget).mockReset();
  });

  it("shows the month's totals, the limit and where the spend went", async () => {
    signIn("member");
    vi.mocked(getUsage).mockResolvedValue(usage());

    renderWithQuery(<UsageOverview />);

    expect(await screen.findByText("Spent this month")).toBeInTheDocument();
    expect(screen.getByText("Within budget")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: /spend used/i })).toHaveAttribute("aria-valuenow", "75");
    expect(screen.getAllByText("acme/widgets").length).toBeGreaterThan(0);
    expect(screen.getByText("llama-3.3-70b")).toBeInTheDocument();
    expect(screen.getByText("Nov 2026")).toBeInTheDocument();
    // Members see the limits but can't change them.
    expect(screen.queryByRole("button", { name: /save limits/i })).not.toBeInTheDocument();
    expect(screen.getByText(/owners and admins can change the limits/i)).toBeInTheDocument();
  });

  it("says plainly when there is no limit", async () => {
    signIn("member");
    vi.mocked(getUsage).mockResolvedValue(
      usage({ budget: { monthly_budget_usd: null, token_cap: null, warn_percent: 80 }, used_percent: null }),
    );

    renderWithQuery(<UsageOverview />);

    expect(await screen.findByText(/no monthly limit is set/i)).toBeInTheDocument();
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
  });

  it("shows a day's figures when its bar is pointed at", async () => {
    signIn("member");
    vi.mocked(getUsage).mockResolvedValue(usage());
    renderWithQuery(<UsageOverview />);

    const bar = await screen.findByRole("button", { name: /^2 Nov:/ });
    await userEvent.hover(bar);

    expect(screen.getByText("2 Nov: $0.75 · 1,500 tokens · 2 reviews")).toBeInTheDocument();
  });

  it("lets an admin save new limits, with an empty field meaning no limit", async () => {
    signIn("admin");
    vi.mocked(getUsage).mockResolvedValue(usage());
    vi.mocked(setBudget).mockResolvedValue({ monthly_budget_usd: 25, token_cap: null, warn_percent: 90 });
    renderWithQuery(<UsageOverview />);

    const spend = await screen.findByLabelText(/monthly spend limit/i);
    await userEvent.clear(spend);
    await userEvent.type(spend, "25");
    const warn = screen.getByLabelText(/warn from/i);
    await userEvent.clear(warn);
    await userEvent.type(warn, "90");
    await userEvent.click(screen.getByRole("button", { name: /save limits/i }));

    await waitFor(() =>
      expect(vi.mocked(setBudget).mock.calls[0][0]).toEqual({
        monthly_budget_usd: "25",
        token_cap: null,
        warn_percent: 90,
      }),
    );
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });
});

describe("BudgetBanner", () => {
  beforeEach(() => vi.mocked(getUsage).mockReset());

  it("stays out of the way within budget", async () => {
    vi.mocked(getUsage).mockResolvedValue(usage());
    const { container } = renderWithQuery(<BudgetBanner />);
    await waitFor(() => expect(getUsage).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("says reviews are paused once the limit is reached, and links to the page", async () => {
    vi.mocked(getUsage).mockResolvedValue(usage({ status: "exceeded", spent_usd: 1.2, used_percent: 120 }));
    renderWithQuery(<BudgetBanner />);

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Limit reached")).toBeInTheDocument();
    expect(within(alert).getByText(/paused until 1 Dec \(\$1\.20 of \$1\.00 used/)).toBeInTheDocument();
    expect(within(alert).getByRole("link", { name: "Usage & Budget" })).toHaveAttribute("href", "/usage");
  });
});
