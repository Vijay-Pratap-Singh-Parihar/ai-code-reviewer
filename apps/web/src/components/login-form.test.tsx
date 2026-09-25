import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LoginForm } from "@/components/login-form";
import { login } from "@/lib/api-client";
import { setAuthState } from "@/lib/auth-store";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, login: vi.fn(), signup: vi.fn() };
});

describe("LoginForm", () => {
  beforeEach(() => {
    replace.mockClear();
    vi.mocked(login).mockReset();
    setAuthState({ accessToken: null, user: null, status: "unauthenticated" });
  });

  it("logs in and navigates to the dashboard on success", async () => {
    vi.mocked(login).mockResolvedValue({
      access_token: "tok",
      token_type: "bearer",
      expires_in: 900,
      user: { id: "u1", org_id: "o1", email: "dev@example.com", role: "owner" },
    });

    render(<LoginForm />);
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "dev@example.com" } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "correct-horse-battery" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"));
    expect(login).toHaveBeenCalledWith({ email: "dev@example.com", password: "correct-horse-battery" });
  });

  it("shows the server's error message and does not navigate on failure", async () => {
    vi.mocked(login).mockRejectedValue(new Error("invalid email or password"));

    render(<LoginForm />);
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "dev@example.com" } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("invalid email or password");
    expect(replace).not.toHaveBeenCalled();
  });
});
