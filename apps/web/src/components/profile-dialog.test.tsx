import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProfileDialog } from "@/components/profile-dialog";
import { updateProfile, type UserPublic } from "@/lib/api-client";
import { getAuthState, setAuthState } from "@/lib/auth-store";

vi.mock("@/lib/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api-client")>();
  return { ...actual, updateProfile: vi.fn() };
});

const unnamed: UserPublic = { id: "u1", org_id: "o1", email: "ada@example.com", role: "owner" };

describe("ProfileDialog", () => {
  beforeEach(() => {
    vi.mocked(updateProfile).mockReset();
    setAuthState({ accessToken: "t", user: unnamed, status: "authenticated" });
  });

  it("shows the person's name and initials, not their email", () => {
    render(<ProfileDialog user={{ ...unnamed, first_name: "Ada", last_name: "Lovelace" }} />);

    const trigger = screen.getByRole("button", { name: /your profile/i });
    expect(trigger).toHaveTextContent("AL");
    expect(trigger).toHaveTextContent("Ada Lovelace");
    expect(trigger).not.toHaveTextContent("ada@example.com");
  });

  it("lets an account without a name add one", async () => {
    const named = { ...unnamed, first_name: "Ada", last_name: "Lovelace" };
    vi.mocked(updateProfile).mockResolvedValue(named);
    const user = userEvent.setup();
    render(<ProfileDialog user={unnamed} />);

    const trigger = screen.getByRole("button", { name: /your profile/i });
    expect(trigger).toHaveTextContent("Add your name");
    await user.click(trigger);
    await user.type(screen.getByLabelText(/first name/i), "Ada");
    await user.type(screen.getByLabelText(/last name/i), "Lovelace");
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(updateProfile).toHaveBeenCalledWith({ first_name: "Ada", last_name: "Lovelace" }));
    expect(getAuthState().user).toEqual(named);
  });
});
