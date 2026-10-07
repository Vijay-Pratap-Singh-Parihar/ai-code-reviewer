import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RepositoryGroups, groupRepositories } from "@/components/repository-groups";
import {
  listInstallations,
  listRepositories,
  type InstallationPublic,
  type RepositoryPublic,
} from "@/lib/api-client";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, listRepositories: vi.fn(), listInstallations: vi.fn() };
});

function repo(overrides: Partial<RepositoryPublic>): RepositoryPublic {
  return {
    id: "r",
    full_name: "acme/widgets",
    default_branch: "main",
    is_active: true,
    connected: true,
    auto_review_enabled: false,
    github_repo_id: 1,
    installation_id: "inst-a",
    ...overrides,
  };
}

function installation(overrides: Partial<InstallationPublic>): InstallationPublic {
  return {
    id: "inst-a",
    installation_id: 1,
    account_login: "acme",
    account_type: "Organization",
    installed_at: "2026-10-01T00:00:00Z",
    repository_count: 1,
    status: "active",
    ...overrides,
  };
}

describe("groupRepositories", () => {
  it("groups by connection, puts unattached repos last and drops empty connections", () => {
    const groups = groupRepositories(
      [
        repo({ id: "1", installation_id: "inst-b", full_name: "bee/one" }),
        repo({ id: "2", installation_id: null, github_repo_id: null, full_name: "solo/manual" }),
        repo({ id: "3", installation_id: "inst-a" }),
      ],
      [installation({}), installation({ id: "inst-b", account_login: "bee" }), installation({ id: "inst-c" })],
    );

    expect(groups.map((g) => [g.installation?.account_login ?? null, g.repos.map((r) => r.id)])).toEqual([
      ["acme", ["3"]],
      ["bee", ["1"]],
      [null, ["2"]],
    ]);
  });
});

describe("RepositoryGroups", () => {
  beforeEach(() => {
    vi.mocked(listRepositories).mockReset();
    vi.mocked(listInstallations).mockReset();
  });

  it("shows each connection as its own section with its status", async () => {
    vi.mocked(listInstallations).mockResolvedValue([
      installation({}),
      installation({ id: "inst-b", account_login: "old-org", status: "uninstalled" }),
    ]);
    vi.mocked(listRepositories).mockResolvedValue([
      repo({ id: "1" }),
      repo({ id: "2", installation_id: "inst-b", full_name: "old-org/thing", connected: false, is_active: false }),
    ]);

    render(
      <QueryClientProvider client={new QueryClient()}>
        <RepositoryGroups />
      </QueryClientProvider>,
    );

    const oldOrg = (await screen.findByText("old-org")).closest("[data-slot=card]") as HTMLElement;
    expect(within(oldOrg).getByText("Uninstalled")).toBeInTheDocument();
    expect(within(oldOrg).getByText("old-org/thing")).toBeInTheDocument();
    expect(within(oldOrg).queryByText("acme/widgets")).not.toBeInTheDocument();
  });

  it("points to Connect GitHub when there are no repositories", async () => {
    vi.mocked(listInstallations).mockResolvedValue([]);
    vi.mocked(listRepositories).mockResolvedValue([]);

    render(
      <QueryClientProvider client={new QueryClient()}>
        <RepositoryGroups />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("link", { name: /connect github/i })).toHaveAttribute("href", "/github");
  });
});
