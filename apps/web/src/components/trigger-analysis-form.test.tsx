import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TriggerAnalysisForm } from "@/components/trigger-analysis-form";
import { triggerAnalysis } from "@/lib/api-client";

async function selectCrossFile() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox"));
  await user.click(await screen.findByText(/cross_file/i));
}

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
  });

  it("submits with agent='diff_only' and no repo_path by default", async () => {
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
    expect(call.repo_path).toBeUndefined();
  });

  it("reveals a required repo_path field once agent is switched to cross_file", async () => {
    renderForm(vi.fn());

    expect(screen.queryByLabelText(/repo path/i)).not.toBeInTheDocument();

    await selectCrossFile();

    expect(screen.getByLabelText(/repo path/i)).toBeInTheDocument();
  });

  it("submits repo_path when agent is cross_file", async () => {
    vi.mocked(triggerAnalysis).mockResolvedValue({
      id: "run-2",
      status: "queued",
      tokens_in: 0,
      tokens_out: 0,
      cost_usd: 0,
      latency_ms: null,
      error: null,
      findings: [],
    });
    renderForm(vi.fn());

    await selectCrossFile();
    fireEvent.change(screen.getByLabelText(/repo path/i), {
      target: { value: "/tmp/acme-widgets" },
    });
    fireEvent.click(screen.getByRole("button", { name: /trigger review/i }));

    await waitFor(() => expect(triggerAnalysis).toHaveBeenCalled());
    const call = vi.mocked(triggerAnalysis).mock.calls[0][0];
    expect(call.agent).toBe("cross_file");
    expect(call.repo_path).toBe("/tmp/acme-widgets");
  });

  it("shows the server's error message on failure", async () => {
    vi.mocked(triggerAnalysis).mockRejectedValue(new Error("no ready branch index"));
    renderForm(vi.fn());

    fireEvent.click(screen.getByRole("button", { name: /trigger review/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("no ready branch index");
  });
});
