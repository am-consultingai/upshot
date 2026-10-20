import type { Page } from "@playwright/test";
import { CSRF, SESSION } from "../playwright.config";
import { expect, gotoApp, isoAt, minutesFromNow, test } from "./fixtures";

/**
 * The first-run tour (z8tj1hmy14): four stops on a sample meeting, then the sample burns.
 *
 * The sample lives only in the page: these check it reaches the screen, never the server,
 * and that only it goes when the tour ends — the user's own meetings and events stay.
 */

const HEADERS = { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` };

/** Every request the page makes that names a sample, in its address or its body: there must be none. */
function watchSampleRequests(page: Page, ...needles: string[]): string[] {
  const seen: string[] = [];
  page.on("request", (request) => {
    const said = `${request.url()} ${request.postData() ?? ""}`;
    if (["tour-sample", ...needles].some((needle) => said.includes(needle))) seen.push(request.url());
  });
  return seen;
}

async function replayFromMenu(page: Page) {
  await page.getByTestId("app-title").click();
  await page.getByTestId("workspace-menu").locator("[data-item=tour]").click();
}

async function next(page: Page) {
  await page.getByTestId("tour-next").click();
}

test("the_tour_walks_four_stops_on_a_sample_and_burns_it_away", async ({ page, seedBody }) => {
  await seedBody({ reset: true });
  const sampleRequests = watchSampleRequests(page);
  await gotoApp(page, "/");
  await expect(page.getByTestId("timeline-empty")).toBeVisible();
  await replayFromMenu(page);

  // 1 · Record. A replay starts here, without the welcome card.
  const tip = page.getByTestId("tour-tip");
  await expect(tip).toHaveAttribute("data-stop", "1");
  await expect(page.getByTestId("tour-count")).toHaveText("1 / 4");
  // The sample is in the sidebar, under its notice.
  await expect(page.getByTestId("rail").getByTestId("tour-sample-note")).toBeVisible();
  await expect(page.getByTestId("rail").getByTestId("tour-sample-tag")).toHaveCount(2);

  // The tour holds the keyboard: Ctrl+R records nothing behind it, from the card or the page.
  const recordCalls: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/recording/")) recordCalls.push(request.url());
  });
  // Aimed at the record button behind the overlay (Joyride's focus trap takes focus back to
  // the card, so Enter there is the card's own Next), then from the card itself.
  await page.evaluate(() => document.querySelector<HTMLElement>("[data-tour=record]")?.focus());
  await page.keyboard.press("Control+r");
  // And from the card's own button.
  await page.getByTestId("tour-next").focus();
  await page.keyboard.press("Control+r");
  await expect(tip).toHaveAttribute("data-stop", "1");
  expect(recordCalls).toEqual([]);
  await next(page);

  // 2 · The week: empty, so sample events fill it.
  await expect(tip).toHaveAttribute("data-stop", "2");
  await expect(page.getByTestId("span-week")).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("[data-testid=calendar-timegrid] [data-event^=tour-sample-]").first()).toBeVisible();

  // 3 · The sample meeting's page, drawn without asking the server.
  await next(page);
  await expect(tip).toHaveAttribute("data-stop", "3");
  await expect(page).toHaveURL(/\/m\/tour-sample-1$/);
  await expect(page.getByTestId("summary-html")).toBeVisible();
  await expect(page.getByTestId("meeting-actions").locator("li")).toHaveCount(3);

  // 4 · AI setup, last, where the user can act.
  await next(page);
  await expect(tip).toHaveAttribute("data-stop", "4");
  await expect(page).toHaveURL(/\/settings#summaries$/);
  await expect(page.getByTestId("tour-next")).toHaveText("Done");
  await next(page);

  // The fire, then nothing of the sample anywhere.
  await expect(page.getByTestId("tour-tip")).toHaveCount(0);
  await expect(page.getByTestId("tour-blocker")).toHaveCount(0, { timeout: 8000 });
  await expect(page.getByTestId("tour-sample-tag")).toHaveCount(0);
  await expect(page.getByTestId("tour-sample-note")).toHaveCount(0);
  await expect(page.getByTestId("timeline-empty")).toBeVisible();
  expect(sampleRequests).toEqual([]);

  const settings = await page.request.get("/api/settings");
  expect((await settings.json()).config.ui.tour_done).toBe(true);
});

test("a_week_with_the_users_events_gets_no_sample_events_and_their_data_stays", async ({ page, seedBody }) => {
  // The user left the calendar on the month: the tour shows the week and remembers nothing.
  await seedBody({
    reset: true,
    meetings: [{ id: "own-standup", title: "Team standup", state: "RENDERED", started_at: isoAt(1, 9), duration_s: 900 }],
    calendar_events: [{ id: "own-interview", title: "Interview: Noa", start: minutesFromNow(60), end: minutesFromNow(120) }],
  });
  await page.request.put("/api/settings", { headers: HEADERS, data: { values: { "ui.calendar_span": "month" } } });
  // The borrowed title is the user's own, so only the tour's lines may carry it: none do.
  const sampleRequests = watchSampleRequests(page, "Team standup");
  await gotoApp(page, "/");
  await expect(page.getByTestId("span-month")).toHaveAttribute("aria-pressed", "true");
  await replayFromMenu(page);
  await next(page);

  // The calendar stop points at the user's own next event; the week gets no samples.
  const tip = page.getByTestId("tour-tip");
  await expect(tip).toHaveAttribute("data-stop", "2");
  await expect(page.locator("[data-testid=calendar-timegrid] [data-event^=tour-sample-]")).toHaveCount(0);
  // The sample recordings borrow the user's titles, and are marked as samples.
  const sampleRows = page.locator("[data-testid=meeting-card][data-meeting-id^=tour-sample-]");
  await expect(sampleRows).toHaveCount(2);
  await expect(sampleRows.first()).toContainText("Team standup");

  // Esc skips; the toast says where the replay is.
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("tour-blocker")).toHaveCount(0, { timeout: 8000 });
  await expect(page.getByTestId("toaster")).toContainText("Replay it from the workspace menu");
  await expect(sampleRows).toHaveCount(0);
  await expect(page.locator("[data-testid=meeting-card][data-meeting-id=own-standup]")).toBeVisible();
  expect(sampleRequests).toEqual([]);
  // Back on the month the user chose, which was never overwritten.
  await expect(page.getByTestId("span-month")).toHaveAttribute("aria-pressed", "true");
  const settings = await page.request.get("/api/settings");
  expect((await settings.json()).config.ui.calendar_span).toBe("month");
  await page.getByTestId("span-week").click();
  await expect(page.getByTestId("calendar-gevent").filter({ hasText: "Interview: Noa" })).toBeVisible();
});

test("after_setup_it_starts_by_itself_once_with_a_welcome_in_any_language", async ({ page, seedBody }) => {
  await seedBody({ reset: true });
  // In Hebrew: the saved language arrives just after the page starts, and once cancelled
  // the start of the tour with it.
  const put = await page.request.put("/api/settings", {
    headers: HEADERS,
    data: { values: { "ui.tour_done": false, "ui.language": "he" } },
  });
  expect(put.ok()).toBeTruthy();
  await gotoApp(page, "/");
  // As setup leaves it: the arrival is marked in the history entry, and read on load.
  await page.evaluate(() => window.history.replaceState({ usr: { celebrate: true }, key: "setup", idx: 0 }, "", "/"));
  await page.reload();

  await expect(page.getByTestId("tour-welcome")).toBeVisible({ timeout: 8000 });
  // Named as they read, not with Joyride's "Next" and "Skip tour".
  for (const id of ["tour-next", "tour-skip"]) {
    const button = page.getByTestId(id);
    await expect(button).toHaveAccessibleName(((await button.textContent()) ?? "").trim());
  }
  await page.getByTestId("tour-skip").click();
  await expect(page.getByTestId("tour-blocker")).toHaveCount(0, { timeout: 8000 });
  await expect(page.getByTestId("tour-sample-tag")).toHaveCount(0);
  const settings = await page.request.get("/api/settings");
  expect((await settings.json()).config.ui.tour_done).toBe(true);
});

test("in_hebrew_the_count_and_keys_read_left_to_right_and_arrows_mirror", async ({ page, seedBody }) => {
  await seedBody({ reset: true });
  await page.request.put("/api/settings", { headers: HEADERS, data: { values: { "ui.language": "he" } } });
  await gotoApp(page, "/");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await replayFromMenu(page);
  const tip = page.getByTestId("tour-tip");
  await expect(tip).toHaveAttribute("data-stop", "1");
  await expect(page.getByTestId("tour-count")).toHaveAttribute("dir", "ltr");
  // Forward is the left arrow, right to left.
  await page.keyboard.press("ArrowLeft");
  await expect(tip).toHaveAttribute("data-stop", "2");
  await page.keyboard.press("ArrowRight");
  await expect(tip).toHaveAttribute("data-stop", "1");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("tour-blocker")).toHaveCount(0, { timeout: 8000 });
});
