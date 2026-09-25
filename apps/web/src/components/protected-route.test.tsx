import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { ProtectedRoute } from "@/components/protected-route";
import { setAuthState } from "@/lib/auth-store";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));

describe("ProtectedRoute", () => {
  beforeEach(() => {
    replace.mockClear();
  });

  it("renders nothing and redirects to /login when unauthenticated", () => {
    setAuthState({ accessToken: null, user: null, status: "unauthenticated" });
    render(
      <ProtectedRoute>
        <p>secret dashboard</p>
      </ProtectedRoute>,
    );

    expect(screen.queryByText("secret dashboard")).not.toBeInTheDocument();
    expect(replace).toHaveBeenCalledWith("/login");
  });

  it("renders nothing (and does not redirect) while the session is still loading", () => {
    setAuthState({ accessToken: null, user: null, status: "loading" });
    render(
      <ProtectedRoute>
        <p>secret dashboard</p>
      </ProtectedRoute>,
    );

    expect(screen.queryByText("secret dashboard")).not.toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("renders children once authenticated", () => {
    setAuthState({
      accessToken: "tok",
      user: { id: "u1", org_id: "o1", email: "dev@example.com", role: "owner" },
      status: "authenticated",
    });
    render(
      <ProtectedRoute>
        <p>secret dashboard</p>
      </ProtectedRoute>,
    );

    expect(screen.getByText("secret dashboard")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });
});
