import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TriggerIndexForm } from "@/components/trigger-index-form";
import { triggerIndex } from "@/lib/api-client";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, triggerIndex: vi.fn() };
});

function renderForm() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <TriggerIndexForm />
    </QueryClientProvider>,
  );
}

describe("TriggerIndexForm", () => {
  beforeEach(() => {
    vi.mocked(triggerIndex).mockReset();
  });

  it("submits the repo/branch/path fields and shows the resulting status", async () => {
    vi.mocked(triggerIndex).mockResolvedValue({
      id: "idx-1",
      repo_id: "repo-1",
      branch_name: "main",
      head_sha: null,
      status: "pending",
      node_count: 0,
      edge_count: 0,
      build_duration_ms: null,
      built_at: null,
      created_at: new Date().toISOString(),
    });
    renderForm();

    fireEvent.click(screen.getByRole("button", { name: /build branch index/i }));

    await waitFor(() => expect(triggerIndex).toHaveBeenCalled());
    expect(vi.mocked(triggerIndex).mock.calls[0][0]).toEqual({
      repo_full_name: "acme/widgets",
      branch_name: "main",
      repo_path: "/tmp/acme-widgets",
    });
    expect(await screen.findByText(/pending/i)).toBeInTheDocument();
  });

  it("shows the server's error message on failure", async () => {
    vi.mocked(triggerIndex).mockRejectedValue(
      new Error("repository is already registered under a different organization"),
    );
    renderForm();

    fireEvent.click(screen.getByRole("button", { name: /build branch index/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already registered/i);
  });
});
