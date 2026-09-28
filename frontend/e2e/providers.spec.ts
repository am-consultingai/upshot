import { CSRF, SESSION } from "../playwright.config";
import { expect, gotoSettings, test } from "./fixtures";

const HEADERS = { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` };

test("provider_settings_list_every_option", async ({ page }) => {
  await gotoSettings(page, "summaries");
  const rows = page.getByTestId("provider-row");
  await expect(rows).toHaveCount(6);
  for (const id of [
    "anthropic",
    "gemini",
    "openai",
    "claude-subscription",
    "codex-subscription",
    "antigravity-subscription",
  ]) {
    await expect(page.locator(`[data-provider="${id}"]`)).toBeVisible();
  }
  // Not offered here for now (product owner, 2026-09-28).
  await expect(page.locator('[data-provider="none"]')).toHaveCount(0);
  await expect(page.locator('[data-provider="ollama"]')).toHaveCount(0);
});

test("each_row_is_one_line_with_its_mark", async ({ page }) => {
  await gotoSettings(page, "summaries");
  const row = page.locator('[data-provider="claude-subscription"]');
  await expect(row.locator("svg").first()).toBeVisible();
  // No explanations under the row: its name, state, action and Test only.
  for (const gone of ["provider-hint", "provider-plan", "provider-path", "provider-account", "provider-detail"]) {
    await expect(row.getByTestId(gone)).toHaveCount(0);
  }
  const box = await row.boundingBox();
  expect(box!.height).toBeLessThan(60);
});

test("api_key_is_stored_and_never_rendered", async ({ page }) => {
  await gotoSettings(page, "summaries");
  const row = page.locator('[data-provider="gemini"]');
  await expect(row.getByTestId("provider-ready")).toHaveText("No key yet");

  await row.getByTestId("provider-key").fill("AIza-SECRET-VALUE");
  await row.getByTestId("provider-save-key").click();
  await expect(row.getByTestId("provider-ready")).toHaveText("Key stored");

  await page.reload();
  const reloaded = page.locator('[data-provider="gemini"]');
  // The status asks each installed CLI (agy asks Google), so the list can take a while.
  await expect(reloaded.getByTestId("provider-ready")).toHaveText("Key stored", { timeout: 20_000 });
  // the value itself never comes back to the browser
  expect(await page.content()).not.toContain("AIza-SECRET-VALUE");
  await expect(reloaded.getByTestId("provider-key")).toHaveValue("");
});

test("switching_provider_persists", async ({ page }) => {
  await gotoSettings(page, "summaries");
  const gemini = page.locator('[data-provider="gemini"]');
  await gemini.getByTestId("provider-select").click();
  await expect(gemini).toHaveAttribute("data-active", "true", { timeout: 10_000 });
  await page.reload();
  await expect(page.getByTestId("provider-settings")).toBeVisible();
  await expect(page.locator('[data-provider="gemini"]')).toHaveAttribute("data-active", "true", {
    timeout: 10_000,
  });
  // Put back the fake the other specs expect.
  await page.request.put("/api/settings", { headers: HEADERS, data: { values: { "llm.provider": "fake" } } });
});

test("test_button_reports_a_real_result", async ({ page }) => {
  await gotoSettings(page, "summaries");
  const gemini = page.locator('[data-provider="gemini"]');
  // clear any key a previous test stored, so this asserts the no-key path deterministically
  await page.request.put("/api/settings/secrets", { headers: HEADERS, data: { values: { gemini: "" } } });
  await page.reload();
  await expect(gemini.getByTestId("provider-ready")).toHaveText("No key yet", { timeout: 20_000 });
  await gemini.getByTestId("provider-test").click();
  const result = gemini.getByTestId("provider-test-result");
  await expect(result).toBeVisible();
  await expect(result).toHaveAttribute("data-ok", "false"); // no key configured
});

test("claude_subscription_shows_install_state", async ({ page }) => {
  await gotoSettings(page, "summaries");
  const row = page.locator('[data-provider="claude-subscription"]');
  const ready = await row.getAttribute("data-ready");
  const signedIn = await row.getAttribute("data-signed-in");
  if (ready === "false") {
    await expect(row.getByTestId("provider-install")).toBeEnabled();
    await expect(row.getByTestId("provider-signin")).toHaveCount(0);
  } else if (signedIn === "true") {
    // Offering "Sign in" to someone already signed in is not a next step.
    await expect(row.getByTestId("provider-signin")).toHaveCount(0);
    await expect(row.getByTestId("provider-signout")).toBeVisible();
  } else {
    await expect(row.getByTestId("provider-signin")).toBeEnabled();
  }
});
