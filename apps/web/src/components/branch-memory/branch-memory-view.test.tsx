import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BranchMemoryView } from "@/components/branch-memory/branch-memory-view";
import { getBranchMemory, indexRepository, type BranchMemoryRow } from "@/lib/api-client";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, getBranchMemory: vi.fn(), indexRepository: vi.fn() };
});

function row(overrides: Partial<BranchMemoryRow> = {}): BranchMemoryRow {
  return {
    repo_id: "repo-1",
    repo_full_name: "acme/widgets",
    repo_active: true,
    branch_name: "main",
    is_default_branch: true,
    state: "ready",
    has_ready_index: true,
    head_sha: "abcdef1234567890",
    node_count: 1200,
    edge_count: 950,
    unresolved_count: 50,
    unresolved_capped: false,
    unresolved_rate: 0.05,
    build_duration_ms: 4200,
    built_at: "2026-11-04T10:00:00Z",
    last_update: {
      mode: "incremental",
      from_sha: "1111111",
      to_sha: "abcdef1",
      files_changed: 3,
      duration_ms: 850,
      reason: null,
      created_at: "2026-11-04T10:00:00Z",
    },
    full_rebuilds_30d: 1,
    incremental_updates_30d: 6,
    ...overrides,
  };
}

function renderView() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <BranchMemoryView />
    </QueryClientProvider>,
  );
}

describe("BranchMemoryView", () => {
  beforeEach(() => {
    vi.mocked(getBranchMemory).mockReset();
    vi.mocked(indexRepository).mockReset();
  });

  it("shows each branch's index: state, size, unresolved rate and last update", async () => {
    vi.mocked(getBranchMemory).mockResolvedValue([
      row(),
      row({
        branch_name: "release",
        is_default_branch: false,
        state: "failed",
        last_update: { ...row().last_update!, mode: "full", reason: "could not resolve branch head" },
      }),
      row({ repo_id: "repo-2", repo_full_name: "acme/never", state: "not_indexed", has_ready_index: false, unresolved_rate: null }),
    ]);

    renderView();

    expect(await screen.findByText("acme/widgets")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getAllByText("1,200")).toHaveLength(2);
    expect(screen.getAllByText("5.0%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("abcdef1").length).toBeGreaterThan(0);
    expect(screen.getByText("Last build failed")).toBeInTheDocument();
    expect(screen.getByText("Reviews use the previous index")).toBeInTheDocument();
    expect(screen.getByText("could not resolve branch head")).toBeInTheDocument();
    expect(screen.getByText("Not indexed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^build the index of acme\/never main/i })).toBeInTheDocument();
  });

  it("asks for a full rebuild of that branch", async () => {
    vi.mocked(getBranchMemory).mockResolvedValue([row({ branch_name: "develop", is_default_branch: false })]);
    vi.mocked(indexRepository).mockResolvedValue({} as never);

    renderView();
    await userEvent.click(await screen.findByRole("button", { name: /rebuild the index of acme\/widgets develop/i }));

    await waitFor(() =>
      expect(indexRepository).toHaveBeenCalledWith("repo-1", { branch_name: "develop", force_full: true }),
    );
  });

  it("points to the GitHub page when nothing is connected", async () => {
    vi.mocked(getBranchMemory).mockResolvedValue([]);
    renderView();
    expect(await screen.findByRole("link", { name: "GitHub" })).toHaveAttribute("href", "/github");
  });
});
