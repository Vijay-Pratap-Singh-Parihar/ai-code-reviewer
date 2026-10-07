import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";

/**
 * Real-browser check of Stage 10's GitHub screens. Works whether or not the
 * stack already has a GitHub App (created via "Create GitHub App"): nothing
 * here needs a signed-in GitHub account or an LLM. The connected-repo flow itself (PR list,
 * one-click review) is covered by unit tests with a faked API, plus the
 * manual real-GitHub check in VERIFICATION.md.
 *
 * The most important case is the login round trip: GitHub's redirect back
 * to /github/setup carries a single-use `code`, so a signed-out (or expired)
 * session must come back to that exact URL after login, not the dashboard.
 */

const STAMP = Date.now();
const ORG_NAME = `Playwright GitHub ${STAMP}`;
const EMAIL = `playwright-github-${STAMP}@example.com`;
const PASSWORD = "correct-horse-battery";

test.describe.configure({ mode: "serial" });

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
    // Best-effort cleanup, same convention as auth.spec.ts.
  }
});

test("sign up, then GitHub/Repositories screens and the collapsed manual form", async ({ page }) => {
  await page.goto("/signup");
  await page.getByLabel("Organization name").fill(ORG_NAME);
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  // Dashboard: no repos yet → points at GitHub; the paste-a-diff form is
  // still there, but collapsed under "Advanced".
  await expect(page.getByRole("link", { name: "Connect GitHub" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Trigger review" })).toBeHidden();
  await page.getByText("Advanced: review a pasted diff").click();
  await expect(page.getByRole("button", { name: "Trigger review" })).toBeVisible();

  // Sidebar links are real now.
  await page.getByRole("link", { name: "Repositories", exact: true }).click();
  await expect(page).toHaveURL(/\/repositories$/);
  await expect(page.getByText("No GitHub repositories connected yet.")).toBeVisible();

  await page.getByRole("link", { name: "GitHub", exact: true }).click();
  await expect(page).toHaveURL(/\/github$/);
  // No App yet → one-click creation; App already set up → install link.
  await expect(
    page.getByRole("button", { name: "Create GitHub App" }).or(page.getByRole("link", { name: "Connect GitHub" })),
  ).toBeVisible();
});

test("a signed-out GitHub callback returns to /github/setup after login", async ({ page }) => {
  const callback = "/github/setup?installation_id=42&code=e2e-code&setup_action=install";
  await page.goto(callback);

  await expect(page).toHaveURL(/\/login\?next=%2Fgithub%2Fsetup/);
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/\/github\/setup\?installation_id=42&code=e2e-code/);
  // The handler really POSTed the values and the API refused them (no App:
  // 503; with an App, GitHub rejects the made-up OAuth code): the page shows
  // the reason instead of hanging or pretending to succeed.
  await expect(page.getByText(/Couldn.t connect this installation:/)).toBeVisible();
});

test("an off-site ?next= is ignored", async ({ page }) => {
  await page.goto("/login?next=//evil.example.com");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/localhost:\d+\/dashboard$/);
});
