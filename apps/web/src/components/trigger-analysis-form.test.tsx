import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TriggerAnalysisForm } from "@/components/trigger-analysis-form";
import { triggerAnalysis } from "@/lib/api-client";
import { saveRunMetadata } from "@/lib/run-metadata-store";

vi.mock("@/lib/run-metadata-store", () => ({ saveRunMetadata: vi.fn() }));

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, triggerAnalysis: vi.fn() };
});

function renderForm(onTriggered: (runId: string) => void) {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <TriggerAnalysisForm onTriggered={onTriggered} />
    </QueryClientProvider>,
  );
}

describe("TriggerAnalysisForm", () => {
  beforeEach(() => {
    vi.mocked(triggerAnalysis).mockReset();
    vi.mocked(saveRunMetadata).mockReset();
  });

  it("stashes the diff/PR metadata for the PR analysis view on success", async () => {
    vi.mocked(triggerAnalysis).mockResolvedValue({
      id: "run-meta",
      status: "queued",
      tokens_in: 0,
      tokens_out: 0,
      cost_usd: 0,
      latency_ms: null,
      error: null,
      findings: [],
    });
    renderForm(vi.fn());

    fireEvent.click(screen.getByRole("button", { name: /trigger review/i }));

    await waitFor(() => expect(saveRunMetadata).toHaveBeenCalledTimes(1));
    const [runId, metadata] = vi.mocked(saveRunMetadata).mock.calls[0];
    expect(runId).toBe("run-meta");
    expect(metadata).toMatchObject({
      repoFullName: "acme/widgets",
      baseBranch: "main",
      prNumber: 1,
      prTitle: "Fix off-by-one",
      agent: "diff_only",
    });
    expect(metadata.diff).toContain("for i in range(n + 1)");
  });

  it("always submits a diff_only review with no worker filesystem path", async () => {
    vi.mocked(triggerAnalysis).mockResolvedValue({
      id: "run-1",
      status: "queued",
      tokens_in: 0,
      tokens_out: 0,
      cost_usd: 0,
      latency_ms: null,
      error: null,
      findings: [],
    });
    const onTriggered = vi.fn();
    renderForm(onTriggered);

    fireEvent.click(screen.getByRole("button", { name: /trigger review/i }));

    await waitFor(() => expect(onTriggered).toHaveBeenCalledWith("run-1"));
    const call = vi.mocked(triggerAnalysis).mock.calls[0][0];
    expect(call.agent).toBe("diff_only");
    expect(call).not.toHaveProperty("repo_path");
  });

  it("offers no cross-file option or repo path field for a pasted diff", () => {
    renderForm(vi.fn());

    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/repo path/i)).not.toBeInTheDocument();
    expect(screen.getByText(/connect the repository through GitHub/i)).toBeInTheDocument();
  });

  it("shows the server's error message on failure", async () => {
    vi.mocked(triggerAnalysis).mockRejectedValue(new Error("diff is required"));
    renderForm(vi.fn());

    fireEvent.click(screen.getByRole("button", { name: /trigger review/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("diff is required");
  });
});
