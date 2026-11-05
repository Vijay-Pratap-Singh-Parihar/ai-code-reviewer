import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HistoryView } from "@/components/history/history-view";
import {
  getAdoptionStats,
  listHistory,
  listRepositories,
  type AdoptionStats,
  type HistoryRun,
} from "@/lib/api-client";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, listHistory: vi.fn(), getAdoptionStats: vi.fn(), listRepositories: vi.fn() };
});

function run(overrides: Partial<HistoryRun> = {}): HistoryRun {
  return {
    id: "r1",
    created_at: "2026-11-04T10:00:00Z",
    finished_at: "2026-11-04T10:01:00Z",
    status: "succeeded",
    agent: "diff_only",
    model: "qwen3.5:4b",
    source: "github",
    trigger: "webhook",
    repo_id: "repo-1",
    repo_full_name: "acme/widgets",
    pr_number: 7,
    pr_title: "Fix the loop bound",
    pr_state: "merged",
    findings: 3,
    severities: { high: 2, low: 1 },
    cost_usd: 0,
    tokens: 1200,
    github_review_url: "https://github.com/acme/widgets/pull/7#pullrequestreview-1",
    error: null,
    ...overrides,
  };
}

const STATS: AdoptionStats = {
  runs: 4,
  succeeded: 3,
  failed: 1,
  in_progress: 0,
  pull_requests: 3,
  merged_pull_requests: 2,
  posted_to_github: 1,
  findings: 5,
  by_severity: { high: 2, low: 3 },
  accepted: 2,
  dismissed: 1,
  ignored: 0,
  no_feedback: 2,
  acceptance_rate: 0.667,
};

function renderView() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <HistoryView />
    </QueryClientProvider>,
  );
}

describe("HistoryView", () => {
  beforeEach(() => {
    vi.mocked(listHistory).mockReset();
    vi.mocked(getAdoptionStats).mockReset().mockResolvedValue(STATS);
    vi.mocked(listRepositories).mockReset().mockResolvedValue([]);
  });

  it("lists past reviews with their findings and links each to its run", async () => {
    vi.mocked(listHistory).mockResolvedValue({ runs: [run()], next_before: null });

    renderView();

    const title = await screen.findByText("Fix the loop bound");
    expect(title.closest("a")).toHaveAttribute("href", "/runs/r1");
    expect(screen.getByText("acme/widgets#7 · merged")).toBeInTheDocument();
    expect(screen.getByText("2 high")).toBeInTheDocument();
    expect(screen.getByText("Automatic")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /on github/i })).toHaveAttribute("href", run().github_review_url);
    expect(screen.queryByRole("button", { name: /load older/i })).not.toBeInTheDocument();
  });

  it("shows adoption statistics", async () => {
    vi.mocked(listHistory).mockResolvedValue({ runs: [], next_before: null });

    renderView();

    expect(await screen.findByText("67%")).toBeInTheDocument();
    expect(screen.getByText("2 of 3 findings with feedback")).toBeInTheDocument();
    expect(screen.getByText("2 merged")).toBeInTheDocument();
    expect(screen.getByText("No reviews match these filters.")).toBeInTheDocument();
  });

  it("loads older reviews from the cursor", async () => {
    vi.mocked(listHistory)
      .mockResolvedValueOnce({ runs: [run()], next_before: "2026-11-04T10:00:00Z" })
      .mockResolvedValueOnce({ runs: [run({ id: "r0", pr_title: "An older change" })], next_before: null });

    renderView();
    await userEvent.click(await screen.findByRole("button", { name: /load older reviews/i }));

    expect(await screen.findByText("An older change")).toBeInTheDocument();
    await waitFor(() =>
      expect(vi.mocked(listHistory).mock.calls[1][0]).toMatchObject({ before: "2026-11-04T10:00:00Z" }),
    );
  });
});
