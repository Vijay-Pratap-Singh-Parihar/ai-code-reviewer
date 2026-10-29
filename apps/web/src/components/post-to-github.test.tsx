import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PostToGitHub } from "@/components/post-to-github";
import { postReviewToGitHub, type AnalysisRunPublic } from "@/lib/api-client";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, postReviewToGitHub: vi.fn() };
});

function run(overrides: Partial<AnalysisRunPublic> = {}): AnalysisRunPublic {
  return {
    id: "run-1",
    status: "succeeded",
    tokens_in: 1,
    tokens_out: 1,
    cost_usd: 0,
    latency_ms: 10,
    error: null,
    findings: [],
    source: "github",
    github_review_url: null,
    post_error: null,
    ...overrides,
  };
}

function renderButton(r: AnalysisRunPublic) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <PostToGitHub run={r} />
    </QueryClientProvider>,
  );
}

describe("PostToGitHub", () => {
  beforeEach(() => vi.mocked(postReviewToGitHub).mockReset());

  it("offers to post a finished pull request review", async () => {
    vi.mocked(postReviewToGitHub).mockResolvedValue(run());
    renderButton(run());

    await userEvent.setup().click(screen.getByRole("button", { name: /post review to github/i }));

    await waitFor(() => expect(postReviewToGitHub).toHaveBeenCalledWith("run-1"));
    expect(await screen.findByRole("button", { name: /posting to github/i })).toBeDisabled();
  });

  it("links to the review once it is on GitHub", () => {
    renderButton(run({ github_review_url: "https://github.com/acme/widgets/pull/7#pullrequestreview-1" }));

    expect(screen.getByRole("link", { name: /posted to github/i })).toHaveAttribute(
      "href",
      "https://github.com/acme/widgets/pull/7#pullrequestreview-1",
    );
    expect(screen.queryByRole("button", { name: /post review/i })).not.toBeInTheDocument();
  });

  it("explains why posting failed and offers to try again", () => {
    renderButton(run({ post_error: "the GitHub App can't write pull request reviews" }));

    expect(screen.getByRole("alert")).toHaveTextContent(/can't write pull request reviews/);
    expect(screen.getByRole("button", { name: /try posting again/i })).toBeEnabled();
  });

  it("shows nothing for pasted diffs or unfinished reviews", () => {
    const { container, rerender } = renderButton(run({ source: "manual" }));
    expect(container).toBeEmptyDOMElement();
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <PostToGitHub run={run({ status: "running" })} />
      </QueryClientProvider>,
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
