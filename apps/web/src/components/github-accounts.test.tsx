import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { GitHubAccounts } from "@/components/github-accounts";
import { getGitHubApp, listInstallations } from "@/lib/api-client";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, getGitHubApp: vi.fn(), listInstallations: vi.fn(), syncInstallation: vi.fn() };
});

function renderAccounts() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <GitHubAccounts />
    </QueryClientProvider>,
  );
}

describe("GitHubAccounts", () => {
  beforeEach(() => {
    vi.mocked(getGitHubApp).mockReset();
    vi.mocked(listInstallations).mockReset().mockResolvedValue([]);
  });

  it("offers one-click App creation when the server has no App yet", async () => {
    vi.mocked(getGitHubApp).mockResolvedValue({ configured: false, install_url: null, webhook_configured: false });

    renderAccounts();

    expect(await screen.findByRole("button", { name: /create github app/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /connect github/i })).not.toBeInTheDocument();
  });

  it("links Connect GitHub to the App's install page and lists installations", async () => {
    vi.mocked(getGitHubApp).mockResolvedValue({
      configured: true,
      install_url: "https://github.com/apps/revu-dev/installations/new",
      webhook_configured: true,
    });
    vi.mocked(listInstallations).mockResolvedValue([
      {
        id: "inst-1",
        installation_id: 42,
        account_login: "acme",
        account_type: "Organization",
        installed_at: "2026-10-07T00:00:00Z",
        repository_count: 3,
      },
    ]);

    renderAccounts();

    expect(await screen.findByRole("link", { name: /connect github/i })).toHaveAttribute(
      "href",
      "https://github.com/apps/revu-dev/installations/new",
    );
    expect(await screen.findByText("acme")).toBeInTheDocument();
    expect(screen.getByText(/3 repositories/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /manage on github/i })).toHaveAttribute(
      "href",
      "https://github.com/organizations/acme/settings/installations/42",
    );
    expect(screen.queryByText(/No webhook secret/)).not.toBeInTheDocument();
  });

  it("warns when webhooks can't be received", async () => {
    vi.mocked(getGitHubApp).mockResolvedValue({
      configured: true,
      install_url: "https://github.com/apps/revu-dev/installations/new",
      webhook_configured: false,
    });

    renderAccounts();

    expect(await screen.findByText(/No webhook secret is configured/)).toBeInTheDocument();
  });
});
