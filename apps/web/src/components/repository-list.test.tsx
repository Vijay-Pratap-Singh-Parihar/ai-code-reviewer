import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RepositoryList } from "@/components/repository-list";
import {
  deleteRepositoryData,
  listRepositories,
  updateRepository,
  type RepositoryPublic,
} from "@/lib/api-client";
import { setAuthState } from "@/lib/auth-store";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, listRepositories: vi.fn(), updateRepository: vi.fn(), deleteRepositoryData: vi.fn() };
});

function repo(overrides: Partial<RepositoryPublic>): RepositoryPublic {
  return {
    id: "repo-1",
    full_name: "acme/widgets",
    default_branch: "main",
    is_active: true,
    connected: true,
    auto_review_enabled: false,
    github_repo_id: 101,
    ...overrides,
  };
}

function renderList() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <RepositoryList />
    </QueryClientProvider>,
  );
}

function signInAs(role: "owner" | "member") {
  setAuthState({
    accessToken: "token",
    status: "authenticated",
    user: { id: "u1", org_id: "o1", email: "me@example.com", role },
  });
}

describe("RepositoryList", () => {
  beforeEach(() => {
    vi.mocked(listRepositories).mockReset();
    vi.mocked(updateRepository).mockReset();
    vi.mocked(deleteRepositoryData).mockReset().mockResolvedValue({ status: "queued" });
    signInAs("owner");
  });
  afterEach(() => setAuthState({ accessToken: null, user: null, status: "unauthenticated" }));

  it("points to Connect GitHub when nothing is connected", async () => {
    vi.mocked(listRepositories).mockResolvedValue([repo({ connected: false, github_repo_id: null })]);

    renderList();

    expect(await screen.findByRole("link", { name: /connect github/i })).toHaveAttribute("href", "/github");
  });

  it("links connected repos and greys out manual-only ones", async () => {
    vi.mocked(listRepositories).mockResolvedValue([
      repo({}),
      repo({ id: "repo-2", full_name: "solo/manual", connected: false, github_repo_id: null }),
    ]);

    renderList();

    expect(await screen.findByRole("link", { name: "acme/widgets" })).toHaveAttribute(
      "href",
      "/repositories/repo-1",
    );
    expect(screen.queryByRole("link", { name: "solo/manual" })).not.toBeInTheDocument();
    expect(screen.getByText("manual")).toBeInTheDocument();
    // Only the connected repo gets an auto-review switch.
    expect(screen.getAllByRole("switch")).toHaveLength(1);
  });

  it("labels a disconnected repo with when its data will be deleted, and lets an owner delete it now", async () => {
    vi.mocked(listRepositories).mockResolvedValue([
      repo({}),
      repo({
        id: "repo-3",
        full_name: "acme/gone",
        connected: false,
        is_active: false,
        github_repo_id: 303,
        disconnected_at: "2026-10-01T00:00:00Z",
        purge_after: "2026-10-31T00:00:00Z",
      }),
    ]);
    const user = userEvent.setup();

    renderList();

    expect(await screen.findByText("disconnected")).toBeInTheDocument();
    expect(screen.getByText(/data deleted automatically from/i)).toBeInTheDocument();
    expect(screen.queryByText("manual")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "acme/gone" })).not.toBeInTheDocument();

    // Only the disconnected repo can be deleted; the connected one can't.
    const [deleteButton] = screen.getAllByRole("button", { name: /delete data now/i });
    await user.click(deleteButton);
    await user.type(await screen.findByLabelText(/to confirm/i), "acme/gone");
    await user.click(screen.getByRole("button", { name: /delete permanently/i }));
    await waitFor(() => expect(deleteRepositoryData).toHaveBeenCalledWith("repo-3"));
  });

  it("shows a paused (suspended) repo without a deletion date", async () => {
    vi.mocked(listRepositories).mockResolvedValue([repo({ is_active: false })]);

    renderList();

    expect(await screen.findByText("paused")).toBeInTheDocument();
    expect(screen.queryByText(/deleted automatically/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete data now/i })).not.toBeInTheDocument();
  });

  it("shows members the auto-review state but doesn't let them change it", async () => {
    signInAs("member");
    vi.mocked(listRepositories).mockResolvedValue([repo({})]);

    renderList();

    // Base UI marks a disabled switch with data-disabled (it isn't a native input).
    expect(await screen.findByRole("switch", { name: /auto-review acme\/widgets/i })).toHaveAttribute(
      "data-disabled",
    );
    expect(screen.getByText(/only owners and admins can change this/i)).toBeInTheDocument();
  });

  it("shows auto-review off by default and turns it on for that repo", async () => {
    vi.mocked(listRepositories).mockResolvedValue([repo({})]);
    vi.mocked(updateRepository).mockResolvedValue(repo({ auto_review_enabled: true }));

    renderList();
    const toggle = await screen.findByRole("switch", { name: /auto-review acme\/widgets/i });
    expect(toggle).not.toBeChecked();

    await userEvent.setup().click(toggle);

    await waitFor(() => expect(updateRepository).toHaveBeenCalledWith("repo-1", { auto_review_enabled: true }));
    await waitFor(() => expect(toggle).toBeChecked());
  });
});
