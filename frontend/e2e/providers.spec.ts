import { expect, gotoSettings, test } from "./fixtures";

test("provider_settings_list_every_option", async ({ page }) => {
  await gotoSettings(page, "summaries");
  const rows = page.getByTestId("provider-row");
  await expect(rows).toHaveCount(6);
  for (const id of ["anthropic", "gemini", "openai", "claude-subscription", "codex-subscription", "ollama"]) {
    await expect(page.locator(`[data-provider="${id}"]`)).toBeVisible();
  }
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
  await expect(reloaded.getByTestId("provider-ready")).toHaveText("Key stored");
  // the value itself never comes back to the browser
  expect(await page.content()).not.toContain("AIza-SECRET-VALUE");
  await expect(reloaded.getByTestId("provider-key")).toHaveValue("");
});

test("switching_provider_persists", async ({ page }) => {
  await gotoSettings(page, "summaries");
  const ollama = page.locator('[data-provider="ollama"]');
  await ollama.getByTestId("provider-select").click();
  await expect(ollama).toHaveAttribute("data-active", "true", { timeout: 10_000 });
  await page.reload();
  await expect(page.getByTestId("provider-settings")).toBeVisible();
  await expect(page.locator('[data-provider="ollama"]')).toHaveAttribute(
    "data-active",
    "true",
    { timeout: 10_000 },
  );
});

test("test_button_reports_a_real_result", async ({ page }) => {
  await gotoSettings(page, "summaries");
  const gemini = page.locator('[data-provider="gemini"]');
  // clear any key a previous test stored, so this asserts the no-key path deterministically
  await gemini.getByTestId("provider-key").fill("");
  await gemini.getByTestId("provider-save-key").click();
  await expect(gemini.getByTestId("provider-ready")).toHaveText("No key yet");
  await gemini.getByTestId("provider-test").click();
  const result = gemini.getByTestId("provider-test-result");
  await expect(result).toBeVisible();
  await expect(result).toHaveAttribute("data-ok", "false"); // no key configured
});

test("claude_subscription_shows_install_state", async ({ page }) => {
  await gotoSettings(page, "summaries");
  const row = page.locator('[data-provider="claude-subscription"]');
  await expect(row.getByTestId("provider-hint")).toBeVisible();
  const ready = await row.getAttribute("data-ready");
  const signedIn = await row.getAttribute("data-signed-in");
  if (ready === "false") {
    await expect(row.getByTestId("provider-install")).toBeEnabled();
    await expect(row.getByTestId("provider-signin")).toBeDisabled();
    await expect(row.getByTestId("provider-hint")).toHaveText(/not installed/i);
  } else if (signedIn === "true") {
    // Offering "Sign in" to someone already signed in is not a next step.
    await expect(row.getByTestId("provider-signin")).toHaveCount(0);
    await expect(row.getByTestId("provider-hint")).toHaveText(/ready to summarize/i);
  } else {
    await expect(row.getByTestId("provider-signin")).toBeEnabled();
    await expect(row.getByTestId("provider-hint")).toHaveText(/never sees your credentials/i);
  }
});
