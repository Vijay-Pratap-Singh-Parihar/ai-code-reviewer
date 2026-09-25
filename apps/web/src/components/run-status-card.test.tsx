import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RunStatusCard } from "@/components/run-status-card";
import { getAnalysisRun } from "@/lib/api-client";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, getAnalysisRun: vi.fn() };
});

function renderCard(runId: string) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <RunStatusCard runId={runId} />
    </QueryClientProvider>,
  );
}

describe("RunStatusCard", () => {
  beforeEach(() => {
    vi.mocked(getAnalysisRun).mockReset();
  });

  it("shows a succeeded run's findings, tokens, and cost", async () => {
    vi.mocked(getAnalysisRun).mockResolvedValue({
      id: "run-1",
      status: "succeeded",
      tokens_in: 356,
      tokens_out: 9,
      cost_usd: 0.0008,
      latency_ms: 1638,
      error: null,
      findings: [
        {
          file_path: "app.py",
          line_start: 3,
          line_end: 3,
          category: "correctness",
          severity: "high",
          message: "off-by-one in the loop bound",
          confidence: 0.9,
          agent_name: "diff_only",
        },
      ],
    });

    renderCard("run-1");

    expect(await screen.findByText("succeeded")).toBeInTheDocument();
    expect(await screen.findByText(/off-by-one in the loop bound/)).toBeInTheDocument();
    expect(screen.getByText(/356 in \/ 9 out tokens/)).toBeInTheDocument();
    expect(screen.getByText("$0.0008")).toBeInTheDocument();
    expect(screen.getByText(/app\.py:3-3/)).toBeInTheDocument();
  });

  it("shows a failed run's error message", async () => {
    vi.mocked(getAnalysisRun).mockResolvedValue({
      id: "run-2",
      status: "failed",
      tokens_in: 0,
      tokens_out: 0,
      cost_usd: 0,
      latency_ms: null,
      error: "provider unavailable",
      findings: [],
    });

    renderCard("run-2");

    expect(await screen.findByText("failed")).toBeInTheDocument();
    expect(await screen.findByText("provider unavailable")).toBeInTheDocument();
  });

  it("shows 'no findings' for a succeeded run with none", async () => {
    vi.mocked(getAnalysisRun).mockResolvedValue({
      id: "run-3",
      status: "succeeded",
      tokens_in: 10,
      tokens_out: 5,
      cost_usd: 0.0001,
      latency_ms: 200,
      error: null,
      findings: [],
    });

    renderCard("run-3");

    expect(await screen.findByText("No findings.")).toBeInTheDocument();
  });
});
