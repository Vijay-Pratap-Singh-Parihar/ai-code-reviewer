import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";

/**
 * Real-browser end-to-end check of the Stage 9 auth shell, against whatever
 * stack `baseURL`/the API point at (see playwright.config.ts) — this is the
 * one thing curl-based verification can't prove: that the httpOnly refresh
 * cookie and the in-memory access token actually survive a real page reload
 * in a real browser, not just that the underlying HTTP contract is correct.
 *
 * Creates one real organization/user per run (a unique email per test run)
 * and deletes it from the dev database afterward via `docker exec psql`,
 * matching this project's established cleanup convention for live
 * verification (see IMPLEMENTATION_PLAN.md's Stage 9 section).
 */

const ORG_NAME = `Playwright E2E ${Date.now()}`;
const EMAIL = `playwright-e2e-${Date.now()}@example.com`;
const PASSWORD = "correct-horse-battery";

test.afterAll(() => {
  try {
    execFileSync("docker", [
      "exec",
      "ai-code-reviewer-postgres-1",
      "psql",
      "-U",
      "revu",
      "-d",
      "revu",
      "-c",
      `DELETE FROM organizations WHERE name = '${ORG_NAME}';`,
    ]);
  } catch {
    // Best-effort cleanup; a missing/renamed container shouldn't fail the run.
  }
});

test("root redirects to /login when signed out", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
});

test("sign up, sign out, and log back in, surviving a real page reload", async ({ page }) => {
  await page.goto("/signup");
  await page.getByLabel("Organization name").fill(ORG_NAME);
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByText(EMAIL)).toBeVisible();

  // The access token lives in memory only; reloading must recover the
  // session from the httpOnly refresh cookie rather than bouncing to
  // /login — this is the real point of running this in an actual browser.
  await page.reload();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByText(EMAIL)).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);

  // A signed-out visitor must not be able to browse back into the dashboard.
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login$/);

  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByText(EMAIL)).toBeVisible();
});

test("wrong password shows an inline error and does not navigate", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill("definitely-wrong");
  await page.getByRole("button", { name: "Sign in" }).click();

  // Next.js's own route announcer also has role="alert", so match the
  // form's error text directly rather than by role.
  await expect(page.getByText(/invalid email or password/i)).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});
