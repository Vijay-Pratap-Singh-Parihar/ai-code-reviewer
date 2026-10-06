import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PullRequestList } from "@/components/pull-request-list";
import { listPullRequests, reviewPullRequest, type PullRequestSummary } from "@/lib/api-client";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, listPullRequests: vi.fn(), reviewPullRequest: vi.fn() };
});

const HEAD = "a".repeat(40);

function pull(overrides: Partial<PullRequestSummary>): PullRequestSummary {
  return {
    number: 7,
    title: "Return two",
    author: "octocat",
    head_sha: HEAD,
    base_branch: "main",
    draft: false,
    html_url: "https://github.com/acme/widgets/pull/7",
    updated_at: null,
    latest_run: null,
    ...overrides,
  };
}

function renderList() {
  return render(
    // No retries: a failed GitHub fetch should surface immediately in tests.
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PullRequestList repoId="repo-1" hasReadyIndex={false} />
    </QueryClientProvider>,
  );
}

describe("PullRequestList", () => {
  beforeEach(() => {
    vi.mocked(listPullRequests).mockReset();
    vi.mocked(reviewPullRequest).mockReset();
    push.mockReset();
  });

  it("reviews a PR with diff_only by default and opens the run", async () => {
    vi.mocked(listPullRequests).mockResolvedValue([pull({})]);
    vi.mocked(reviewPullRequest).mockResolvedValue({
      id: "run-9",
      status: "queued",
      tokens_in: 0,
      tokens_out: 0,
      cost_usd: 0,
      latency_ms: null,
      error: null,
      findings: [],
    });

    renderList();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Review" }));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/runs/run-9"));
    expect(reviewPullRequest).toHaveBeenCalledWith("repo-1", 7, "diff_only");
  });

  it("shows the latest run and whether it covers the current head", async () => {
    vi.mocked(listPullRequests).mockResolvedValue([
      pull({ latest_run: { id: "run-1", status: "succeeded", agent: "diff_only", head_sha: HEAD } }),
      pull({
        number: 8,
        title: "Older",
        draft: true,
        latest_run: { id: "run-2", status: "failed", agent: "diff_only", head_sha: "b".repeat(40) },
      }),
    ]);

    renderList();

    expect(await screen.findByText("latest commit")).toBeInTheDocument();
    expect(screen.getByText("older commit")).toBeInTheDocument();
    expect(screen.getByText("draft")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Review again" })).toHaveLength(2);
    expect(screen.getByText("succeeded").closest("a")).toHaveAttribute("href", "/runs/run-1");
  });

  it("surfaces a GitHub error from the server", async () => {
    const { ApiError } = await import("@/lib/api-client");
    vi.mocked(listPullRequests).mockRejectedValue(new ApiError(502, "GitHub request failed: rate limited"));

    renderList();

    expect(await screen.findByRole("alert")).toHaveTextContent(/rate limited/);
  });

  it("says so when there are no open PRs", async () => {
    vi.mocked(listPullRequests).mockResolvedValue([]);

    renderList();

    expect(await screen.findByText("No open pull requests.")).toBeInTheDocument();
  });
});
