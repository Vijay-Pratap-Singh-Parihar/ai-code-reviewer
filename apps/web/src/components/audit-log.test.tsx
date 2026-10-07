import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuditLog } from "@/components/audit-log";
import { listAuditEntries, type AuditEntry } from "@/lib/api-client";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, listAuditEntries: vi.fn() };
});

function entry(overrides: Partial<AuditEntry>): AuditEntry {
  return {
    id: "e1",
    at: "2026-10-08T10:00:00Z",
    action: "review.requested",
    target: "pull_request:acme/widgets#7",
    actor_id: "u1",
    actor_email: "dev@acme.test",
    metadata: { agent: "diff_only" },
    ...overrides,
  };
}

function renderLog() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AuditLog />
    </QueryClientProvider>,
  );
}

describe("AuditLog", () => {
  beforeEach(() => vi.mocked(listAuditEntries).mockReset());

  it("lists events with readable labels, the actor or System, and details", async () => {
    vi.mocked(listAuditEntries).mockResolvedValue({
      entries: [
        entry({}),
        entry({
          id: "e2",
          action: "data.purged",
          target: "repository:acme/old",
          actor_id: null,
          actor_email: null,
          metadata: { reason: "retention", runs_deleted: 3 },
        }),
      ],
      next_before: null,
    });

    renderLog();

    expect(await screen.findByText("Review requested")).toBeInTheDocument();
    expect(screen.getByText("Data deleted")).toBeInTheDocument();
    expect(screen.getByText("dev@acme.test")).toBeInTheDocument();
    expect(screen.getByText("System")).toBeInTheDocument();
    expect(screen.getByText("reason: retention · runs deleted: 3")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /load older/i })).not.toBeInTheDocument();
  });

  it("loads older events from the cursor the API returned", async () => {
    vi.mocked(listAuditEntries)
      .mockResolvedValueOnce({ entries: [entry({})], next_before: "2026-10-08T10:00:00Z" })
      .mockResolvedValueOnce({
        entries: [entry({ id: "e0", action: "auth.login", target: "user:dev@acme.test" })],
        next_before: null,
      });

    renderLog();
    await userEvent.setup().click(await screen.findByRole("button", { name: /load older events/i }));

    expect(await screen.findByText("Signed in")).toBeInTheDocument();
    expect(listAuditEntries).toHaveBeenLastCalledWith({
      limit: 50,
      before: "2026-10-08T10:00:00Z",
      action: null,
    });
  });

  it("says so when nothing has been recorded", async () => {
    vi.mocked(listAuditEntries).mockResolvedValue({ entries: [], next_before: null });

    renderLog();

    await waitFor(() => expect(screen.getByText(/no events recorded yet/i)).toBeInTheDocument());
  });
});
