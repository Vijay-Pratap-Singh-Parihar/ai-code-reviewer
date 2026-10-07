import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RepositoryList } from "@/components/repository-list";
import { listRepositories, updateRepository, type RepositoryPublic } from "@/lib/api-client";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, listRepositories: vi.fn(), updateRepository: vi.fn() };
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

describe("RepositoryList", () => {
  beforeEach(() => {
    vi.mocked(listRepositories).mockReset();
    vi.mocked(updateRepository).mockReset();
  });

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

  it("labels a repo whose App was uninstalled as access removed, not manual", async () => {
    vi.mocked(listRepositories).mockResolvedValue([
      repo({}),
      repo({ id: "repo-3", full_name: "acme/gone", connected: false, is_active: false, github_repo_id: 303 }),
    ]);

    renderList();

    expect(await screen.findByText("access removed")).toBeInTheDocument();
    expect(screen.queryByText("manual")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "acme/gone" })).not.toBeInTheDocument();
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
