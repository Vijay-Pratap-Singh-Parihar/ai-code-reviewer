import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AnalyticsWidgets } from "@/components/analytics-widgets";
import { getAnalysisRun, type AnalysisRunPublic } from "@/lib/api-client";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, getAnalysisRun: vi.fn() };
});

function renderWidgets(runIds: string[]) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <AnalyticsWidgets runIds={runIds} />
    </QueryClientProvider>,
  );
}

function run(overrides: Partial<AnalysisRunPublic>): AnalysisRunPublic {
  return {
    id: "run",
    status: "succeeded",
    tokens_in: 0,
    tokens_out: 0,
    cost_usd: 0,
    latency_ms: null,
    error: null,
    findings: [],
    ...overrides,
  };
}

describe("AnalyticsWidgets", () => {
  beforeEach(() => {
    vi.mocked(getAnalysisRun).mockReset();
  });

  it("shows zeros with no runs yet", () => {
    renderWidgets([]);

    expect(screen.getByText("Runs")).toBeInTheDocument();
    // "Runs" tile's own value and every other tile's value is 0.
    expect(screen.getAllByText("0").length).toBeGreaterThan(0);
  });

  it("aggregates status, findings, and cost across multiple runs", async () => {
    vi.mocked(getAnalysisRun).mockImplementation((runId: string) => {
      if (runId === "r1") {
        return Promise.resolve(
          run({ id: "r1", status: "succeeded", cost_usd: 0.003, findings: [{} as never, {} as never] }),
        );
      }
      if (runId === "r2") {
        return Promise.resolve(run({ id: "r2", status: "failed", error: "boom" }));
      }
      return Promise.resolve(run({ id: "r3", status: "running" }));
    });

    renderWidgets(["r1", "r2", "r3"]);

    // "Runs" (runIds.length) is available on the very first render; wait on
    // a value that only appears once the mocked queries have resolved.
    expect(await screen.findByText("$0.0030")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument(); // Runs
    // Succeeded, Failed, In progress should each read 1.
    expect(screen.getAllByText("1").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText("2")).toBeInTheDocument(); // Findings
  });
});
