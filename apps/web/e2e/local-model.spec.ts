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
 * OLLAMA_URL is the endpoint as the API (not the browser) reaches it:
 * http://127.0.0.1:11434/v1 for a natively run API, the default
 * http://host.docker.internal:11434/v1 when the API runs in Docker.
 */

const OLLAMA_URL = process.env.OLLAMA_URL ?? "http://host.docker.internal:11434/v1";
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
  await form.getByLabel("Name").fill("Local Ollama");
  await form.getByLabel("Endpoint URL").fill(OLLAMA_URL);
  await form.getByRole("button", { name: "Add provider" }).click();
  await expect(form).toBeHidden();

  // 2. Test it with the model: it must reply and return JSON.
  await page.getByRole("button", { name: "Test" }).click();
  const tester = page.getByRole("dialog");
  await tester.getByLabel("Model").fill(MODEL);
  await tester.getByRole("button", { name: "Run test" }).click();
  await expect(tester.getByText("Ready for reviews.")).toBeVisible({ timeout: 5 * 60_000 });
  await page.keyboard.press("Escape");

  // 3. Route reviews to it, thinking off.
  await page.getByRole("combobox", { name: "Review provider" }).click();
  await page.getByRole("option", { name: "Local Ollama" }).click();
  await page.getByLabel("Review model").fill(MODEL);
  await page.getByRole("combobox", { name: "Review thinking" }).click();
  await page.getByRole("option", { name: "Off (fastest)" }).click();
  await page.getByRole("button", { name: "Save models" }).click();
  await expect(page.getByText("Saved.")).toBeVisible();
  await page.reload();
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
