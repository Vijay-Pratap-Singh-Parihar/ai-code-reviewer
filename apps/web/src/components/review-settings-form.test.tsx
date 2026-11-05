import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReviewSettingsForm } from "@/components/review-settings-form";
import { updateRepository, type RepositoryPublic } from "@/lib/api-client";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, updateRepository: vi.fn() };
});

const REPO: RepositoryPublic = {
  id: "repo-1",
  full_name: "acme/widgets",
  default_branch: "main",
  is_active: true,
  connected: true,
  auto_review_enabled: false,
  github_repo_id: 1,
  review_settings: { ignore_globs: ["docs/**"], min_severity: "low", max_comments: null, max_tokens_per_review: null },
};

function renderForm(canEdit: boolean) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ReviewSettingsForm repo={REPO} canEdit={canEdit} />
    </QueryClientProvider>,
  );
}

describe("ReviewSettingsForm", () => {
  beforeEach(() => vi.mocked(updateRepository).mockReset());

  it("saves ignore patterns and limits, with empty fields meaning no limit", async () => {
    vi.mocked(updateRepository).mockResolvedValue(REPO);
    renderForm(true);

    const globs = screen.getByLabelText(/ignore these files/i);
    expect(globs).toHaveValue("docs/**");
    await userEvent.type(globs, "\n*.snap\n\n");
    await userEvent.type(screen.getByLabelText(/token limit per review/i), "50000");
    await userEvent.click(screen.getByRole("button", { name: /save settings/i }));

    await waitFor(() =>
      expect(updateRepository).toHaveBeenCalledWith("repo-1", {
        review_settings: {
          ignore_globs: ["docs/**", "*.snap"],
          min_severity: "low",
          max_comments: null,
          max_tokens_per_review: 50000,
        },
      }),
    );
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("is read-only for members", () => {
    renderForm(false);
    expect(screen.getByLabelText(/ignore these files/i)).toBeDisabled();
    expect(screen.queryByRole("button", { name: /save settings/i })).not.toBeInTheDocument();
    expect(screen.getByText(/owners and admins can change/i)).toBeInTheDocument();
  });
});
