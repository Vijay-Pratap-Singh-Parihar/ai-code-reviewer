import { StrictMode } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CreateGitHubApp } from "@/components/create-github-app";
import { GitHubAppCreatedHandler } from "@/components/github-app-created-handler";
import { ApiError, completeAppManifest, startAppManifest } from "@/lib/api-client";
import { submitManifestForm } from "@/lib/github-manifest";

vi.mock("@/lib/github-manifest", () => ({ submitManifestForm: vi.fn() }));
vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, startAppManifest: vi.fn(), completeAppManifest: vi.fn() };
});

function wrap(node: React.ReactNode) {
  return render(
    <StrictMode>
      <QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>
    </StrictMode>,
  );
}

describe("CreateGitHubApp", () => {
  beforeEach(() => {
    vi.mocked(startAppManifest).mockReset();
    vi.mocked(submitManifestForm).mockReset();
  });

  it("posts the server-built manifest to GitHub", async () => {
    vi.mocked(startAppManifest).mockResolvedValue({
      action_url: "https://github.com/settings/apps/new?state=s",
      manifest: { name: "revu-abc" },
    });
    wrap(<CreateGitHubApp />);

    await userEvent.setup().click(screen.getByRole("button", { name: /create github app/i }));

    await waitFor(() =>
      expect(submitManifestForm).toHaveBeenCalledWith("https://github.com/settings/apps/new?state=s", {
        name: "revu-abc",
      }),
    );
    expect(startAppManifest).toHaveBeenCalledWith(undefined);
  });

  it("passes an organization through and shows server errors", async () => {
    vi.mocked(startAppManifest).mockRejectedValue(new ApiError(422, "not a valid GitHub organization name"));
    wrap(<CreateGitHubApp />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/organization/i), "acme-inc");
    await user.click(screen.getByRole("button", { name: /create github app/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/not a valid GitHub organization/);
    expect(startAppManifest).toHaveBeenCalledWith("acme-inc");
    expect(submitManifestForm).not.toHaveBeenCalled();
  });
});

describe("GitHubAppCreatedHandler", () => {
  beforeEach(() => {
    vi.mocked(completeAppManifest).mockReset();
  });

  it("completes once (StrictMode) and offers the install step", async () => {
    vi.mocked(completeAppManifest).mockResolvedValue({
      configured: true,
      install_url: "https://github.com/apps/revu-abc/installations/new",
      webhook_configured: true,
      source: "database",
      slug: "revu-abc",
    });

    wrap(<GitHubAppCreatedHandler code="code-1" state="state-1" />);

    expect(await screen.findByRole("link", { name: /install on your repositories/i })).toHaveAttribute(
      "href",
      "https://github.com/apps/revu-abc/installations/new",
    );
    expect(completeAppManifest).toHaveBeenCalledTimes(1);
    expect(vi.mocked(completeAppManifest).mock.calls[0][0]).toEqual({ code: "code-1", state: "state-1" });
  });

  it("shows why completion failed", async () => {
    vi.mocked(completeAppManifest).mockImplementation(async () => {
      throw new ApiError(400, "invalid or expired state");
    });

    wrap(<GitHubAppCreatedHandler code="code-2" state="bad" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/invalid or expired state/);
  });

  it("explains missing parameters without calling the API", () => {
    wrap(<GitHubAppCreatedHandler code={null} state={null} />);

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(completeAppManifest).not.toHaveBeenCalled();
  });
});
