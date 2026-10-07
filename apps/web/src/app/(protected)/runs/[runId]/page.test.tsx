import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import RunPage from "./page";
import { getAnalysisRun, type AnalysisRunPublic } from "@/lib/api-client";

vi.mock("next/navigation", () => ({ useParams: () => ({ runId: "run-1" }) }));
// No sessionStorage metadata: the page must work from the API alone.
vi.mock("@/lib/run-metadata-store", () => ({ useRunMetadata: () => null }));
vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, getAnalysisRun: vi.fn() };
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
});
