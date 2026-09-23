/**
 * Hover, tooltips, the tooltip switch, the AI agents section and the open-items balloon.
 *
 * Hover is checked in a browser that reports touch as its primary input, because that
 * is where it failed: Tailwind v4 put every `hover:` style inside `@media (hover:
 * hover)`, which a touchscreen Windows laptop answers "no" — so on the machine this was
 * reported from nothing highlighted, while every test browser (a plain desktop Chrome)
 * saw it work.
 */
import type { Locator, Page } from "@playwright/test";
import { BASE_URL, CSRF, SESSION } from "../playwright.config";
import { expect, gotoApp, test } from "./fixtures";
import { parityBody } from "./parity-data";

const HEADERS = { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` };

test.beforeEach(async ({ seedBody, request }) => {
  await seedBody(parityBody());
  await request.put("/api/settings", {
    headers: HEADERS,
    data: { values: { "detection.decided": true, "llm.provider": "fake" } },
  });
});

const fill = (locator: Locator) => locator.evaluate((node) => getComputedStyle(node).backgroundColor);

async function changesOnHover(page: Page, locator: Locator): Promise<void> {
  await page.mouse.move(2, 2);
  const rest = await fill(locator);
  await locator.hover();
  await expect.poll(() => fill(locator), { message: "hovering should change the fill" }).not.toBe(rest);
}

test("hover_highlights_rows_on_a_touch_first_device", async ({ browser }) => {
  const context = await browser.newContext({ baseURL: BASE_URL, hasTouch: true });
  const page = await context.newPage();
  // The condition that broke it: the device says its primary pointer cannot hover.
  expect(await page.evaluate(() => matchMedia("(hover: hover)").matches)).toBe(false);

  await gotoApp(page, "/");
  await changesOnHover(page, page.locator("[data-testid=meeting-card][data-meeting-id=m-exec]"));
  await changesOnHover(page, page.getByTestId("nav-actions"));
  const chip = page.locator("[data-testid=calendar-gevent][data-recorded=false]").first();
  await changesOnHover(page, chip);
  // The row's `⋯` is revealed on hover too.
  const row = page.locator("[data-testid=meeting-card][data-meeting-id=m-q4]");
  await row.hover();
  await expect(row.locator(".ma-slot")).toHaveCSS("opacity", "1");

  await gotoApp(page, "/m/m-onboarding");
  await changesOnHover(page, page.getByTestId("meeting-actions").getByTestId("action-item").first());
  await changesOnHover(page, page.getByTestId("rail-related").first());

  await gotoApp(page, "/actions");
  await changesOnHover(page, page.getByTestId("action-item").nth(1));
  await context.close();
});

test("key_features_explain_themselves_on_hover", async ({ page }) => {
  await gotoApp(page, "/");
  const tip = page.getByTestId("tooltip");

  await page.getByTestId("start-recording").hover();
  await expect(tip).toContainText("Record a meeting");
  await expect(tip).toContainText("Records both sides");

  await page.getByTestId("nav-actions").hover();
  await expect(tip).toContainText("grouped by when it is due");

  await page.getByTestId("rail-record-this").hover();
  await expect(tip).toContainText("already linked to this calendar event");

  await gotoApp(page, "/m/m-onboarding");
  await page.getByTestId("meeting-tab-transcript").hover();
  await expect(tip).toContainText("Click a timestamp to play from there");
  await page.getByTestId("chip-people").hover();
  await expect(tip).toContainText("correct a wrong match");
  await page.getByTestId("ask-meeting").locator("h2").hover();
  await expect(tip).toContainText("points to the moments in the recording");

  await gotoApp(page, "/actions");
  await page.getByTestId("actions-tab-everyone").hover();
  await expect(tip).toContainText("waiting on from others");
  // It gets out of the way: moving off closes it.
  await page.mouse.move(2, 400);
  await expect(tip).toHaveCount(0);
});

test("the_ai_section_is_called_ai_agents_and_says_what_it_is_for", async ({ page }) => {
  await gotoApp(page, "/settings#summaries");
  const entry = page.getByTestId("settings-section-summaries");
  await expect(entry).toHaveText(/AI agents/);
  await entry.hover();
  await expect(page.getByTestId("tooltip")).toContainText("summarizes transcripts");
  await expect(page.locator("h1")).toHaveText("AI agents");
});

test("tooltips_can_be_turned_off_and_stay_off", async ({ page, request }) => {
  try {
    await gotoApp(page, "/settings#appearance");
    const box = page.getByTestId("ui-tooltips-off");
    // Off by default: tooltips show.
    await expect(box).not.toBeChecked();
    await page.getByTestId("nav-search-icon").hover();
    await expect(page.getByTestId("tooltip")).toBeVisible();

    await box.check();
    await page.mouse.move(600, 600);
    await page.getByTestId("nav-search-icon").hover();
    await page.waitForTimeout(800);
    await expect(page.getByTestId("tooltip")).toHaveCount(0);

    // Saved, not just toggled in this tab.
    await page.reload();
    await expect(page.getByTestId("ui-tooltips-off")).toBeChecked();
    await page.getByTestId("start-recording").hover();
    await page.waitForTimeout(800);
    await expect(page.getByTestId("tooltip")).toHaveCount(0);
    // The controls still have their names without them.
    await expect(page.getByTestId("nav-search-icon")).toHaveAccessibleName("Search");

    await page.getByTestId("ui-tooltips-off").uncheck();
    await page.getByTestId("nav-search-icon").hover();
    await expect(page.getByTestId("tooltip")).toBeVisible();
  } finally {
    await request.put("/api/settings", { headers: HEADERS, data: { values: { "ui.tooltips_off": false } } });
  }
});

test("the_balloon_counts_open_items_and_follows_the_ticks", async ({ page }) => {
  await gotoApp(page, "/");
  const chip = page.locator("[data-testid=calendar-gevent][data-event=ev-onboarding]");
  if ((await chip.count()) === 0) await page.getByTestId("calendar-prev").click();
  const balloon = chip.getByTestId("chip-open");
  await expect(balloon).toHaveText("2");
  await balloon.hover();
  await expect(page.getByTestId("tooltip")).toContainText("nobody has ticked off yet");

  // Tick one on the meeting: the balloon drops to 1; tick the other: it goes.
  await gotoApp(page, "/m/m-onboarding");
  const open = page.locator("[data-testid=meeting-actions] [data-testid=action-item][data-done=false]");
  await open.first().getByTestId("action-toggle").click();
  await page.getByTestId("nav-timeline").click();
  if ((await chip.count()) === 0) await page.getByTestId("calendar-prev").click();
  await expect(balloon).toHaveText("1");
  await gotoApp(page, "/m/m-onboarding");
  await open.first().getByTestId("action-toggle").click();
  await page.getByTestId("nav-timeline").click();
  if ((await chip.count()) === 0) await page.getByTestId("calendar-prev").click();
  await expect(chip).toBeVisible();
  await expect(balloon).toHaveCount(0);
});
