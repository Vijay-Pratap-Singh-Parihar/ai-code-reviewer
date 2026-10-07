import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";

/**
 * Real-browser check of the connection-lifecycle and audit screens against
 * the running stack (no GitHub account or LLM needed): an owner sees the
 * Audit log, sign-ins and failed sign-ins land in it and can be filtered,
 * and Repositories groups by connection. Deleting real connection data is
 * covered by the API, worker and unit tests rather than here, since it
 * needs a GitHub App installation to exist first.
 */

const STAMP = Date.now();
const ORG_NAME = `Playwright Lifecycle ${STAMP}`;
const EMAIL = `playwright-lifecycle-${STAMP}@example.com`;
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
    // Best-effort cleanup, same convention as the other specs.
  }
});

test("an owner's sign-ins are audited and filterable; repositories are grouped", async ({ page, context }) => {
  await page.goto("/signup");
  await page.getByLabel("Organization name").fill(ORG_NAME);
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  // A brand-new organization has nothing in its audit log yet.
  await page.getByRole("link", { name: "Audit log" }).click();
  await expect(page).toHaveURL(/\/audit$/);
  await expect(page.getByText("No events recorded yet.")).toBeVisible();

  // Sign out (drop the session), fail once, then sign in properly.
  await context.clearCookies();
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  await page.getByRole("link", { name: "Audit log" }).click();
  const rows = page.getByRole("row");
  await expect(rows.filter({ hasText: "Signed in" })).toHaveCount(1);
  await expect(rows.filter({ hasText: "Failed sign-in" })).toHaveCount(1);
  await expect(rows.filter({ hasText: EMAIL }).first()).toBeVisible();

  // Filter down to failed sign-ins only.
  await page.getByRole("combobox").click();
  await page.getByRole("option", { name: "Failed sign-in" }).click();
  await expect(rows.filter({ hasText: "Signed in" })).toHaveCount(0);
  await expect(rows.filter({ hasText: "Failed sign-in" })).toHaveCount(1);

  // Repositories: nothing connected yet, so it points at GitHub.
  await page.getByRole("link", { name: "Repositories", exact: true }).click();
  await expect(page.getByText(/grouped by account/)).toBeVisible();
  await expect(page.getByRole("link", { name: "Connect GitHub" })).toBeVisible();
});
