import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";

/**
 * The whole "bring your own model" path in a real browser, with a model
 * running on this machine: add an Ollama provider from the UI, test it,
 * choose it for reviews with thinking switched off, and get a real review.
 *
 * Opt-in, because it needs Ollama serving the model and takes about a
 * minute on a laptop GPU (see docs/setup/local-llm-ollama.md):
 *
 *   E2E_OLLAMA=1 npx playwright test e2e/local-model.spec.ts
 *
 * The "Ollama on this machine" preset already uses the host the API can
 * reach (Docker or not); set OLLAMA_URL only for Ollama elsewhere.
 */

const MODEL = process.env.OLLAMA_MODEL ?? "qwen3.5:4b";
const STAMP = Date.now();
const ORG_NAME = `Playwright local model ${STAMP}`;
const EMAIL = `playwright-local-${STAMP}@example.com`;
const PASSWORD = "correct-horse-battery";

const DIFF = `diff --git a/stats.py b/stats.py
new file mode 100644
--- /dev/null
+++ b/stats.py
@@ -0,0 +1,2 @@
+def average(xs):
+    return sum(xs) / len(xs)
`;

test.skip(!process.env.E2E_OLLAMA, "set E2E_OLLAMA=1 with Ollama serving the model");
test.setTimeout(10 * 60_000);

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
    // Best-effort cleanup.
  }
});

test("a local model, configured only from the UI, reviews a diff with thinking off", async ({ page }) => {
  await page.goto("/signup");
  await page.getByLabel("First name").fill("Playwright");
  await page.getByLabel("Last name").fill("Tester");
  await page.getByLabel("Organization name").fill(ORG_NAME);
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  // 1. Add the provider: no key, just the endpoint.
  await page.getByRole("link", { name: "AI Providers", exact: true }).click();
  await page.getByRole("button", { name: "Add provider" }).click();
  const form = page.getByRole("dialog");
  await form.getByRole("combobox", { name: "Provider" }).click();
  await page.getByRole("option", { name: /OpenAI-compatible/ }).click();
  // The preset carries the host the API can reach, and names the provider.
  await form.getByRole("button", { name: "Ollama on this machine" }).click();
  await expect(form.getByLabel("Display name")).toHaveValue("Local Ollama");
  if (process.env.OLLAMA_URL) await form.getByLabel("Endpoint URL").fill(process.env.OLLAMA_URL);
  await form.getByRole("button", { name: "Add provider" }).click();
  await expect(form).toBeHidden();

  // 2. Test it, picking the model from the list the server reports, with
  // thinking off (the default for a local model); each call shows live.
  await page.getByRole("button", { name: "Test" }).click();
  const tester = page.getByRole("dialog");
  await tester.getByLabel("Model", { exact: true }).click();
  await page.getByRole("option", { name: MODEL, exact: true }).click();
  await expect(tester.getByRole("combobox", { name: "Thinking" })).toContainText("Off (fastest)");
  await tester.getByRole("button", { name: "Run test" }).click();
  await expect(tester.getByRole("button", { name: "Stop" })).toBeVisible();
  await expect(tester.getByText("Ready for reviews.")).toBeVisible({ timeout: 5 * 60_000 });
  await expect(tester.getByLabel("passed").first()).toBeVisible();

  // 3. Route reviews to it straight from the result.
  await tester.getByRole("button", { name: "Use for reviews" }).click();
  await expect(tester.getByText(`Reviews now use ${MODEL}`)).toBeVisible();
  await page.keyboard.press("Escape");
  await page.reload();
  await expect(page.getByLabel("Review model")).toHaveValue(MODEL);
  await expect(page.getByRole("combobox", { name: "Review thinking" })).toContainText("Thinking: Off (fastest)");

  // 4. A real review of a pasted diff, end to end through the worker.
  await page.getByRole("link", { name: "Dashboard", exact: true }).click();
  await page.getByText("Advanced: review a pasted diff").click();
  await page.getByLabel("Diff").fill(DIFF);
  await page.getByLabel("PR title").fill("Add an average helper");
  await page.getByRole("button", { name: "Trigger review" }).click();
  await expect(page.getByText(/^(succeeded|failed)$/)).toBeVisible({ timeout: 8 * 60_000 });
  await expect(page.getByText("succeeded", { exact: true })).toBeVisible();
  await expect(page.getByText(/\d+ in \/ \d+ out tokens/)).toBeVisible();
});
