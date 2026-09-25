import { defineConfig, devices } from "@playwright/test";

/**
 * Runs against an already-running stack (`docker compose up -d`, or
 * `npm run dev` + the API separately) rather than starting its own server —
 * this project's dev workflow is Docker-first, and the point of this suite
 * is to exercise the real browser/cookie/CORS behavior against the real
 * API, not a mocked one. See README.md's "Manual and automated UI testing"
 * section for how to bring the stack up first.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
