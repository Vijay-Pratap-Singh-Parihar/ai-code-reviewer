import { describe, it, expect, afterEach } from "vitest";
import { loginPathReturningHere, postLoginPath, safeNextPath } from "@/lib/redirect";

describe("safeNextPath", () => {
  it("keeps same-origin paths, including their query string", () => {
    expect(safeNextPath("/github/setup?installation_id=1&code=abc")).toBe(
      "/github/setup?installation_id=1&code=abc",
    );
  });

  it.each([null, "", "https://evil.com", "//evil.com", "/\\evil.com", "javascript:alert(1)"])(
    "falls back to /dashboard for %s",
    (raw) => {
      expect(safeNextPath(raw)).toBe("/dashboard");
    },
  );
});

describe("login round trip", () => {
  afterEach(() => window.history.replaceState(null, "", "/"));

  it("encodes the current location into ?next= and decodes it after login", () => {
    window.history.replaceState(null, "", "/github/setup?installation_id=42&code=c0de");
    const loginPath = loginPathReturningHere();
    expect(loginPath).toBe("/login?next=%2Fgithub%2Fsetup%3Finstallation_id%3D42%26code%3Dc0de");

    window.history.replaceState(null, "", loginPath);
    expect(postLoginPath()).toBe("/github/setup?installation_id=42&code=c0de");
  });

  it("uses a bare /login from the dashboard", () => {
    window.history.replaceState(null, "", "/dashboard");
    expect(loginPathReturningHere()).toBe("/login");
  });
});
