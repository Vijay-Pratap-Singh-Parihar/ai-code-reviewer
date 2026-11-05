import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import RunPage from "./page";
import { getAnalysisRun, retryAnalysis, type AnalysisRunPublic } from "@/lib/api-client";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useParams: () => ({ runId: "run-1" }),
  useRouter: () => ({ push }),
}));
// No sessionStorage metadata: the page must work from the API alone.
vi.mock("@/lib/run-metadata-store", () => ({ useRunMetadata: () => null }));
vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, getAnalysisRun: vi.fn(), retryAnalysis: vi.fn() };
});

const BASE_RUN: AnalysisRunPublic = {
  id: "run-1",
  status: "succeeded",
  tokens_in: 100,
  tokens_out: 20,
  cost_usd: 0.001,
  latency_ms: 900,
  error: null,
  findings: [],
};

function renderPage() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <RunPage />
    </QueryClientProvider>,
  );
}

describe("RunPage", () => {
  beforeEach(() => {
    vi.mocked(getAnalysisRun).mockReset();
    vi.mocked(retryAnalysis).mockReset();
    push.mockReset();
  });

  it("offers to run a failed review again and opens the new run", async () => {
    vi.mocked(getAnalysisRun).mockResolvedValue({
      ...BASE_RUN,
      status: "failed",
      error: "The AI provider could not be reached.",
    });
    vi.mocked(retryAnalysis).mockResolvedValue({ ...BASE_RUN, id: "run-2", status: "queued", retry_of: "run-1" });

    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /run review again/i }));

    expect(retryAnalysis).toHaveBeenCalledWith("run-1", {});
    expect(push).toHaveBeenCalledWith("/runs/run-2");
  });

  it("offers 'Run anyway' for a review stopped at the repository's token limit", async () => {
    vi.mocked(getAnalysisRun).mockResolvedValue({
      ...BASE_RUN,
      status: "failed",
      error: "This review would use about 90,000 tokens, over this repository's limit of 50,000 per review.",
      over_ceiling: true,
      estimated_tokens: 90000,
    });
    vi.mocked(retryAnalysis).mockResolvedValue({ ...BASE_RUN, id: "run-2", status: "queued", retry_of: "run-1" });

    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /run anyway/i }));

    expect(retryAnalysis).toHaveBeenCalledWith("run-1", { allow_over_ceiling: true });
    expect(screen.getByText("estimated 90,000 tokens before review")).toBeInTheDocument();
  });

  it("has no retry button on a review that succeeded, and links a retry to its failure", async () => {
    vi.mocked(getAnalysisRun).mockResolvedValue({ ...BASE_RUN, retry_of: "run-0" });

    renderPage();

    const link = await screen.findByRole("link", { name: /earlier review that failed/i });
    expect(link).toHaveAttribute("href", "/runs/run-0");
    expect(screen.queryByRole("button", { name: /run review again/i })).not.toBeInTheDocument();
  });

  it("renders PR context and the diff stored on the server", async () => {
    vi.mocked(getAnalysisRun).mockResolvedValue({
      ...BASE_RUN,
      agent: "diff_only",
      repo_full_name: "acme/widgets",
      pr_number: 7,
      pr_title: "Return two",
      base_branch: "main",
      head_sha: "a".repeat(40),
      diff: "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n",
    });

    renderPage();

    expect(await screen.findByRole("heading", { name: "Return two" })).toBeInTheDocument();
    expect(screen.getByText(/acme\/widgets · main · PR #7 · diff_only/)).toBeInTheDocument();
    expect(await screen.findByText("x = 2")).toBeInTheDocument();
  });

  it("explains a missing diff for runs that predate server-side storage", async () => {
    vi.mocked(getAnalysisRun).mockResolvedValue(BASE_RUN);

    renderPage();

    expect(await screen.findByText(/predates server-side diff storage/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Run run-1" })).toBeInTheDocument();
  });

  it("lists how the review went: files left out, parts", async () => {
    vi.mocked(getAnalysisRun).mockResolvedValue({
      ...BASE_RUN,
      status: "succeeded",
      notices: ["Left out 1 lockfile or generated file: yarn.lock.", "Large diff: reviewed in 3 parts."],
    });

    renderPage();

    const notes = await screen.findByRole("list", { name: "Review notes" });
    expect(notes).toHaveTextContent("yarn.lock");
    expect(notes).toHaveTextContent("reviewed in 3 parts");
  });
});
