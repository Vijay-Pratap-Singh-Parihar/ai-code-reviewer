import { StrictMode } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { GitHubSetupHandler } from "@/components/github-setup-handler";
import { ApiError, linkInstallation } from "@/lib/api-client";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, linkInstallation: vi.fn() };
});

const INSTALLATION = {
  id: "inst-1",
  installation_id: 42,
  account_login: "acme",
  account_type: "Organization",
  installed_at: "2026-10-07T00:00:00Z",
  repository_count: 2,
};

function renderHandler(props: { installationId: string | null; code: string | null; setupAction?: string | null }) {
  return render(
    <StrictMode>
      <QueryClientProvider client={new QueryClient()}>
        <GitHubSetupHandler setupAction={null} {...props} />
      </QueryClientProvider>
    </StrictMode>,
  );
}

describe("GitHubSetupHandler", () => {
  beforeEach(() => {
    vi.mocked(linkInstallation).mockReset();
    replace.mockReset();
  });

  it("links the installation exactly once (even under StrictMode) and goes to repositories", async () => {
    vi.mocked(linkInstallation).mockResolvedValue(INSTALLATION);

    renderHandler({ installationId: "42", code: "code-once" });

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/repositories"));
    // The OAuth code is single-use: a second POST would fail with a spent code.
    expect(linkInstallation).toHaveBeenCalledTimes(1);
    expect(vi.mocked(linkInstallation).mock.calls[0][0]).toEqual({ installation_id: 42, code: "code-once" });
  });

  it("shows the API's reason when linking is refused", async () => {
    vi.mocked(linkInstallation).mockRejectedValue(
      new ApiError(403, "your GitHub account cannot access this installation"),
    );

    renderHandler({ installationId: "42", code: "code-refused" });

    expect(await screen.findByRole("alert")).toHaveTextContent(/cannot access this installation/);
    expect(replace).not.toHaveBeenCalled();
  });

  it("explains the App setting when GitHub sent no code", () => {
    renderHandler({ installationId: "42", code: null });

    expect(screen.getByRole("alert")).toHaveTextContent(/Request user authorization/);
    expect(linkInstallation).not.toHaveBeenCalled();
  });

  it("rejects a non-numeric installation id without calling the API", () => {
    renderHandler({ installationId: "42abc", code: "x" });

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(linkInstallation).not.toHaveBeenCalled();
  });

  it("explains a pending approval request", () => {
    renderHandler({ installationId: null, code: null, setupAction: "request" });

    expect(screen.getByRole("status")).toHaveTextContent(/waiting for an organization owner/);
    expect(linkInstallation).not.toHaveBeenCalled();
  });
});
