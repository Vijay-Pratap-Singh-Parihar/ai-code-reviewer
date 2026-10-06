import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeToggle } from "@/components/theme-toggle";

const setTheme = vi.fn();
let resolvedTheme = "light";

vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme, setTheme }),
}));

describe("ThemeToggle", () => {
  beforeEach(() => {
    setTheme.mockReset();
    resolvedTheme = "light";
  });

  it("switches to dark when the current resolved theme is light", () => {
    resolvedTheme = "light";
    render(<ThemeToggle />);

    fireEvent.click(screen.getByRole("button", { name: /toggle theme/i }));

    expect(setTheme).toHaveBeenCalledWith("dark");
  });

  it("switches to light when the current resolved theme is dark", () => {
    resolvedTheme = "dark";
    render(<ThemeToggle />);

    fireEvent.click(screen.getByRole("button", { name: /toggle theme/i }));

    expect(setTheme).toHaveBeenCalledWith("light");
  });
});
