import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { EvidenceTrail } from "@/components/evidence-trail";
import { giveFindingFeedback, type AnalysisRunPublic, type FindingPublic } from "@/lib/api-client";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, giveFindingFeedback: vi.fn() };
});

const FINDING: FindingPublic = {
  id: "f1",
  file_path: "app.py",
  line_start: 3,
  line_end: 4,
  category: "correctness",
  severity: "high",
  message: "off-by-one in the loop bound",
  confidence: 0.9,
  agent_name: "diff_only",
  evidence: [],
  developer_action: null,
  feedback_reason: null,
  feedback_at: null,
};

/** The run page reads findings from the ["analysis-run", runId] query. */
function RunFindings({ client }: { client: QueryClient }) {
  const run = client.getQueryData<AnalysisRunPublic>(["analysis-run", "r1"]);
  return <EvidenceTrail findings={run?.findings ?? []} runId="r1" />;
}

function renderWithRun() {
  const client = new QueryClient();
  client.setQueryData(["analysis-run", "r1"], { id: "r1", findings: [FINDING] } as unknown as AnalysisRunPublic);
  const view = render(
    <QueryClientProvider client={client}>
      <RunFindings client={client} />
    </QueryClientProvider>,
  );
  const rerender = () =>
    view.rerender(
      <QueryClientProvider client={client}>
        <RunFindings client={client} />
      </QueryClientProvider>,
    );
  return { client, rerender };
}

describe("FindingFeedback", () => {
  beforeEach(() => vi.mocked(giveFindingFeedback).mockReset());

  it("accepts a finding in one click and updates the run", async () => {
    vi.mocked(giveFindingFeedback).mockResolvedValue({ ...FINDING, developer_action: "accepted" });
    const { client, rerender } = renderWithRun();

    await userEvent.click(screen.getByRole("button", { name: /accept/i }));

    await waitFor(() =>
      expect(client.getQueryData<AnalysisRunPublic>(["analysis-run", "r1"])?.findings[0].developer_action).toBe(
        "accepted",
      ),
    );
    expect(giveFindingFeedback).toHaveBeenCalledWith("r1", "f1", { action: "accepted" });
    rerender();
    expect(screen.getByText("Accepted")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /undo/i })).toBeInTheDocument();
  });

  it("after a dismissal, asks why and records the reason", async () => {
    vi.mocked(giveFindingFeedback)
      .mockResolvedValueOnce({ ...FINDING, developer_action: "dismissed" })
      .mockResolvedValueOnce({ ...FINDING, developer_action: "dismissed", feedback_reason: "incorrect" });
    const { rerender } = renderWithRun();

    await userEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    await waitFor(() => expect(giveFindingFeedback).toHaveBeenCalledTimes(1));
    rerender();
    await userEvent.click(await screen.findByRole("button", { name: "Incorrect" }));

    await waitFor(() =>
      expect(giveFindingFeedback).toHaveBeenLastCalledWith("r1", "f1", { action: "dismissed", reason: "incorrect" }),
    );
    rerender();
    expect(screen.getByText(/Dismissed · Incorrect/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /incorrect/i })).toHaveAttribute("aria-pressed", "true");
  });

  it("shows nothing to click without a run (e.g. a preview)", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <EvidenceTrail findings={[FINDING]} />
      </QueryClientProvider>,
    );
    expect(screen.queryByRole("button", { name: /accept/i })).not.toBeInTheDocument();
  });
});
