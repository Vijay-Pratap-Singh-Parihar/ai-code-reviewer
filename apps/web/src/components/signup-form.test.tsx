import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SignupForm } from "@/components/signup-form";
import { signup } from "@/lib/api-client";
import { getAuthState, setAuthState } from "@/lib/auth-store";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, signup: vi.fn() };
});

function fill(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("SignupForm", () => {
  beforeEach(() => {
    replace.mockClear();
    vi.mocked(signup).mockReset();
    setAuthState({ accessToken: null, user: null, status: "unauthenticated" });
  });

  it("creates the account with the person's first and last name", async () => {
    const user = { id: "u1", org_id: "o1", email: "ada@example.com", first_name: "Ada", last_name: "Lovelace", role: "owner" };
    vi.mocked(signup).mockResolvedValue({ access_token: "tok", token_type: "bearer", expires_in: 900, user });

    render(<SignupForm />);
    fill(/first name/i, " Ada ");
    fill(/last name/i, "Lovelace");
    fill(/organization name/i, "Acme");
    fill(/email/i, "ada@example.com");
    fill(/password/i, "correct-horse-battery");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"));
    expect(signup).toHaveBeenCalledWith({
      first_name: "Ada",
      last_name: "Lovelace",
      org_name: "Acme",
      email: "ada@example.com",
      password: "correct-horse-battery",
    });
    expect(getAuthState().user).toEqual(user);
  });

  it("asks for a name before sending anything", async () => {
    render(<SignupForm />);
    fill(/organization name/i, "Acme");
    fill(/email/i, "ada@example.com");
    fill(/password/i, "correct-horse-battery");
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter your first and last name.");
    expect(signup).not.toHaveBeenCalled();
  });
});
