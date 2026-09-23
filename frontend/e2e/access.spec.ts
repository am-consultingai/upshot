/**
 * Who can open the app, and what it says still needs setting up.
 *
 * The one-time-link sign-in was removed on 2026-09-23 (D57): any browser on this
 * computer gets the app. What must still hold is that a *change* needs the CSRF token,
 * which a browser now receives on its first request — so these specs start from a
 * browser that has never been here and has no cookies at all.
 */
import { BASE_URL, CSRF, SESSION } from "../playwright.config";
import { expect, gotoApp, isoAt, test } from "./fixtures";

const HEADERS = { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` };

test("a_browser_with_no_cookies_opens_the_app_and_can_change_things", async ({ browser, seedBody }) => {
  await seedBody({
    reset: true,
    meetings: [
      {
        id: "e2e-open",
        title: "Open to anyone here",
        state: "RENDERED",
        started_at: isoAt(0, 9),
        action_items: [{ who: "ME", what: "Tick me from a fresh browser" }],
      },
    ],
  });
  const context = await browser.newContext({ baseURL: BASE_URL });
  const page = await context.newPage();
  await page.goto("/actions");
  await expect(page.getByText("Authorize this browser")).toHaveCount(0);
  await expect(page.getByTestId("action-item")).toHaveCount(1);
  // A mutation: the page was handed the CSRF cookie and sends it back.
  await page.getByTestId("action-toggle").click();
  await expect(page.getByTestId("actions-count-done")).toHaveText("1");
  await page.reload();
  await expect(page.getByTestId("actions-count-done")).toHaveText("1");
  await context.close();
});

test("a_change_without_the_csrf_token_is_still_refused", async ({ playwright }) => {
  const stranger = await playwright.request.newContext({ baseURL: BASE_URL });
  expect((await stranger.get("/api/status")).status()).toBe(200);
  expect((await stranger.post("/api/recording/start", { data: {} })).status()).toBe(403);
  await stranger.dispose();
});

test("settings_says_what_still_needs_setting_up", async ({ page, request, seed }) => {
  await seed([]);
  // This build can connect a calendar and none is connected, so that is flagged.
  await gotoApp(page, "/");
  await expect(page.getByTestId("settings-warning")).toBeVisible();
  await expect(page.getByTestId("settings-warning")).toHaveAttribute("aria-label", /No calendar is connected/);
  await page.getByTestId("nav-settings").click();
  await expect(page.getByTestId("settings-warning-calendar")).toBeVisible();
  await expect(page.getByTestId("settings-warning-summaries")).toHaveCount(0);

  // A summarizer chosen with no key behind it is flagged too, on its own section.
  try {
    await request.put("/api/settings", { headers: HEADERS, data: { values: { "llm.provider": "gemini" } } });
    await page.reload();
    await expect(page.getByTestId("settings-warning-summaries")).toBeVisible();
    await expect(page.getByTestId("settings-warning")).toHaveAttribute("aria-label", /No AI agent is set up/);
  } finally {
    // Leave the fake in place: a real provider left selected stalls later specs.
    await request.put("/api/settings", { headers: HEADERS, data: { values: { "llm.provider": "fake" } } });
  }
});
