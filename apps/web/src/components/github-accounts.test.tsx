import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { GitHubAccounts } from "@/components/github-accounts";
import {
  deleteInstallationData,
  getGitHubApp,
  listInstallations,
  type InstallationPublic,
} from "@/lib/api-client";
import { setAuthState } from "@/lib/auth-store";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return {
    ...actual,
    getGitHubApp: vi.fn(),
    listInstallations: vi.fn(),
    syncInstallation: vi.fn(),
    deleteInstallationData: vi.fn(),
  };
});

const CONFIGURED = {
  configured: true,
  install_url: "https://github.com/apps/revu-dev/installations/new",
  webhook_configured: true,
};

function signInAs(role: "owner" | "admin" | "member", isPlatformAdmin = false) {
  setAuthState({
    accessToken: "token",
    status: "authenticated",
    user: { id: "u1", org_id: "o1", email: "me@example.com", role, is_platform_admin: isPlatformAdmin },
  });
}

function installation(overrides: Partial<InstallationPublic> = {}): InstallationPublic {
  return {
    id: "inst-1",
    installation_id: 42,
    account_login: "acme",
    account_type: "Organization",
    installed_at: "2026-10-07T00:00:00Z",
    repository_count: 3,
    status: "active",
    disconnected_repository_count: 0,
    purge_after: null,
    ...overrides,
  };
}

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
    vi.mocked(deleteInstallationData).mockReset().mockResolvedValue({ status: "queued" });
    signInAs("owner");
  });
  afterEach(() => setAuthState({ accessToken: null, user: null, status: "unauthenticated" }));

  it("offers one-click App creation to a platform admin when the server has no App yet", async () => {
    signInAs("owner", true);
    vi.mocked(getGitHubApp).mockResolvedValue({ configured: false, install_url: null, webhook_configured: false });

    renderAccounts();

    expect(await screen.findByRole("button", { name: /create github app/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /connect github/i })).not.toBeInTheDocument();
  });

  it("tells anyone else to ask their platform administrator instead", async () => {
    vi.mocked(getGitHubApp).mockResolvedValue({ configured: false, install_url: null, webhook_configured: false });

    renderAccounts();

    expect(await screen.findByText(/github isn't set up yet/i)).toBeInTheDocument();
    expect(screen.getByText(/only a platform administrator can create it/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /create github app/i })).not.toBeInTheDocument();
  });

  it("links Connect GitHub to the App's install page and lists installations", async () => {
    vi.mocked(getGitHubApp).mockResolvedValue(CONFIGURED);
    vi.mocked(listInstallations).mockResolvedValue([installation()]);

    renderAccounts();

    expect(await screen.findByRole("link", { name: /connect github/i })).toHaveAttribute(
      "href",
      "https://github.com/apps/revu-dev/installations/new",
    );
    expect(await screen.findByText("acme")).toBeInTheDocument();
    expect(screen.getByText(/3 active repositories/)).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /manage on github/i })).toHaveAttribute(
      "href",
      "https://github.com/organizations/acme/settings/installations/42",
    );
    expect(screen.queryByText(/No webhook secret/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete data now/i })).not.toBeInTheDocument();
  });

  it("warns when webhooks can't be received", async () => {
    vi.mocked(getGitHubApp).mockResolvedValue({ ...CONFIGURED, webhook_configured: false });

    renderAccounts();

    expect(await screen.findByText(/No webhook secret is configured/)).toBeInTheDocument();
  });

  it("explains a suspended connection keeps its data", async () => {
    vi.mocked(getGitHubApp).mockResolvedValue(CONFIGURED);
    vi.mocked(listInstallations).mockResolvedValue([installation({ status: "suspended" })]);

    renderAccounts();

    expect(await screen.findByText("Suspended")).toBeInTheDocument();
    expect(screen.getByText(/reviews are paused and nothing is deleted/i)).toBeInTheDocument();
  });

  it("shows an uninstalled connection's purge date and lets an owner delete it after typing its name", async () => {
    vi.mocked(getGitHubApp).mockResolvedValue(CONFIGURED);
    vi.mocked(listInstallations).mockResolvedValue([
      installation({
        status: "uninstalled",
        uninstalled_at: "2026-10-01T00:00:00Z",
        repository_count: 0,
        disconnected_repository_count: 2,
        purge_after: "2026-10-31T00:00:00Z",
      }),
    ]);
    const user = userEvent.setup();

    renderAccounts();

    expect(await screen.findByText("Uninstalled")).toBeInTheDocument();
    expect(screen.getByText(/2 disconnected repositories: data is deleted automatically from/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /sync repos/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /delete data now/i }));
    const confirm = await screen.findByRole("button", { name: /delete permanently/i });
    expect(confirm).toBeDisabled();
    await user.type(screen.getByLabelText(/to confirm/i), "acm");
    expect(confirm).toBeDisabled();
    await user.type(screen.getByLabelText(/to confirm/i), "e");
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    await waitFor(() => expect(deleteInstallationData).toHaveBeenCalledWith("inst-1"));
  });

  it("does not offer deletion to members", async () => {
    signInAs("member");
    vi.mocked(getGitHubApp).mockResolvedValue(CONFIGURED);
    vi.mocked(listInstallations).mockResolvedValue([
      installation({ status: "uninstalled", disconnected_repository_count: 1, purge_after: "2026-10-31T00:00:00Z" }),
    ]);

    renderAccounts();

    expect(await screen.findByText("Uninstalled")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete data now/i })).not.toBeInTheDocument();
  });
});
