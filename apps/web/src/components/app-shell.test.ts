import { describe, it, expect } from "vitest";
import { sectionTitle } from "@/components/app-shell";

describe("sectionTitle", () => {
  it.each([
    ["/dashboard", "Dashboard"],
    ["/repositories", "Repositories"],
    ["/repositories/abc", "Repositories"],
    ["/github/setup", "GitHub"],
    ["/runs/abc", "Review"],
    ["/providers", "AI Providers"],
    ["/audit", "Audit log"],
  ])("labels %s as %s", (path, title) => {
    expect(sectionTitle(path)).toBe(title);
  });
});
