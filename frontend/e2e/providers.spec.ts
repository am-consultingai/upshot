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

test("the_chosen_providers_models_are_offered_and_a_choice_is_saved", async ({ page }) => {
  // The list is the provider's own (2026-10-12); here it stands in for Gemini's API.
  const listed = {
    provider: "gemini",
    models: [
      { id: "gemini-3-pro", label: "Gemini 3 Pro" },
      { id: "gemini-3-flash", label: "Gemini 3 Flash" },
    ],
    source: "live",
    fetched_at: 1,
    error: "",
    status: null,
    chosen: { summary: "", assistant: "" },
    automatic: { summary: "", assistant: "" },
    effective: { summary: "", assistant: "" },
  };
  const saved: unknown[] = [];
  await page.route("**/api/llm/models?**", (route) => route.fulfill({ json: listed }));
  await page.route("**/api/llm/model", async (route) => {
    const body = route.request().postDataJSON() as { use: "summary" | "assistant"; model: string };
    saved.push(body);
    await route.fulfill({ json: { ...listed, chosen: { ...listed.chosen, [body.use]: body.model } } });
  });
  await page.request.put("/api/settings/secrets", { headers: HEADERS, data: { values: { gemini: "test-key" } } });
  await page.request.put("/api/settings", { headers: HEADERS, data: { values: { "llm.provider": "gemini" } } });
  try {
    await gotoSettings(page, "summaries");
    const choice = page.getByTestId("model-choice");
    await expect(choice).toBeVisible();
    const summary = choice.getByTestId("model-summary");
    await expect(summary.locator("option")).toHaveText([
      "Automatic (the provider's default)",
      "Gemini 3 Pro",
      "Gemini 3 Flash",
    ]);
    await summary.selectOption("gemini-3-pro");
    await expect.poll(() => saved).toEqual([{ provider: "gemini", use: "summary", model: "gemini-3-pro" }]);
    await expect(summary).toHaveValue("gemini-3-pro");
    // The conversation's model is chosen in the assistant panel, not here.
    await expect(choice.getByTestId("model-assistant")).toHaveCount(0);
  } finally {
    await page.request.put("/api/settings", { headers: HEADERS, data: { values: { "llm.provider": "fake" } } });
    await page.request.put("/api/settings/secrets", { headers: HEADERS, data: { values: { gemini: "" } } });
  }
});

test("a_model_the_server_refuses_says_why_and_a_choice_without_a_list_still_shows", async ({ page }) => {
  const listed = {
    provider: "gemini",
    models: [] as { id: string; label: string }[],
    source: "live",
    fetched_at: 1,
    error: "refused",
    status: 401,
    chosen: { summary: "gemini-old", assistant: "" },
    automatic: { summary: "", assistant: "" },
    effective: { summary: "gemini-old", assistant: "" },
  };
  await page.route("**/api/llm/models?**", (route) => route.fulfill({ json: listed }));
  await page.route("**/api/llm/model", (route) =>
    route.fulfill({ status: 400, json: { detail: "not-offered" } }),
  );
  await page.request.put("/api/settings/secrets", { headers: HEADERS, data: { values: { gemini: "test-key" } } });
  await page.request.put("/api/settings", { headers: HEADERS, data: { values: { "llm.provider": "gemini" } } });
  try {
    await gotoSettings(page, "summaries");
    const choice = page.getByTestId("model-choice");
    // The list failed, but the chosen model is what runs: the select says so.
    await expect(choice.getByTestId("model-summary")).toHaveValue("gemini-old");
    await expect(choice.getByTestId("model-list-error")).toContainText("401");
    // A refused choice says why: here the list comes back and the server will not take
    // a model from it (as when the provider dropped it meanwhile).
    await page.unroute("**/api/llm/models?**");
    await page.route("**/api/llm/models?**", (route) =>
      route.fulfill({ json: { ...listed, error: "", status: null, models: [{ id: "gemini-3", label: "Gemini 3" }] } }),
    );
    await choice.getByTestId("model-refresh").click();
    await choice.getByTestId("model-summary").selectOption("gemini-3");
    await expect(choice.getByTestId("model-save-error")).toHaveText(
      "The provider does not offer that model. Refresh the list.",
    );
  } finally {
    await page.request.put("/api/settings", { headers: HEADERS, data: { values: { "llm.provider": "fake" } } });
    await page.request.put("/api/settings/secrets", { headers: HEADERS, data: { values: { gemini: "" } } });
  }
});
