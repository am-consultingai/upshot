/**
 * Empty states, export beside Copy, sticky day headers and folded nights.
 *
 * Every empty screen used to be one grey sentence. Each now names the situation and
 * offers the next step; these specs hold the next step in place, because that is
 * the part a later edit could quietly drop while the sentence survived.
 */
import { CSRF, SESSION } from "../playwright.config";
import { expect, gotoApp, isoAt, reset, test } from "./fixtures";

const HEADERS = { "X-CSRF-Token": CSRF, Cookie: `up_session=${SESSION}; up_csrf=${CSRF}` };

test.afterEach(async ({ request }) => {
  await reset(request);
});

test("an_empty_library_says_what_the_app_does_and_offers_the_record_button", async ({ page, seed }) => {
  await seed([]);
  await gotoApp(page, "/");
  const empty = page.getByTestId("timeline-empty");
  await expect(empty).toContainText("No meetings yet");
  await expect(empty).toContainText("transcript");
  await empty.getByTestId("timeline-empty-record").click();
  // The one button that starts it does start it: the details dialog opens over a
  // recording that is already running, as it does from the sidebar's own button.
  await expect(page.getByTestId("meeting-info")).toBeVisible();
  await expect(page.getByTestId("recording-bar")).toBeVisible();
  // And stopped again: the reset clears the library, not a recording left running.
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("meeting-info")).toHaveCount(0);
  await page.getByTestId("stop-recording").click();
  await expect(page.getByTestId("recording-bar")).toHaveCount(0);
});

test("a_summary_can_be_exported_from_the_bar_beside_copy", async ({ page, seed }) => {
  await seed([
    {
      id: "e2e-export",
      title: "Pricing review",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      summary_html: "<p>We agreed to hold prices until March.</p>",
    },
  ]);
  await gotoApp(page, "/m/e2e-export");
  await expect(page.getByTestId("copy-summary-bar")).toBeVisible();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByTestId("export-bar").click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/\.md$/);
  // Still on the transcript pill, and still in the ⋯ menu.
  await page.getByTestId("meeting-tab-transcript").click();
  await expect(page.getByTestId("export-bar")).toBeVisible();
  await page.getByTestId("overflow-menu-trigger").click();
  await expect(page.locator("[data-testid=overflow-menu-item][data-item=markdown]")).toBeVisible();
});

test("a_meeting_with_a_transcript_and_no_summary_offers_to_summarize", async ({ page, seed }) => {
  await seed([
    {
      id: "e2e-nosum",
      title: "Unsummarized",
      state: "TRANSCRIBED",
      started_at: isoAt(0, 10),
      turns: [{ speaker: "ME", at_ms: 0, text: "let us ship it on Thursday" }],
    },
  ]);
  await gotoApp(page, "/m/e2e-nosum");
  const empty = page.getByTestId("no-summary");
  await expect(empty).toBeVisible();
  await expect(empty.getByTestId("no-summary-summarize")).toBeVisible();
  // And the other way forward: the transcript, one click away.
  await empty.getByRole("button", { name: "Read the transcript" }).click();
  await expect(page.getByTestId("transcript")).toContainText("ship it on Thursday");
});

test("a_search_that_finds_nothing_says_where_it_looked_and_offers_a_way_out", async ({ page, seed }) => {
  await seed([
    {
      id: "e2e-search",
      title: "Quarterly planning",
      state: "RENDERED",
      started_at: isoAt(0, 10),
      turns: [{ speaker: "ME", at_ms: 0, text: "the budget is approved" }],
    },
  ]);
  await gotoApp(page, "/search");
  await page.getByTestId("search-input").fill("zebracorn");
  const none = page.getByTestId("search-none");
  await expect(none).toContainText("zebracorn");
  await none.getByRole("button").click();
  await expect(page.getByTestId("search-input")).toHaveValue("");

  // Found, but not in the scope chosen: it says so and offers every kind.
  await page.getByTestId("search-input").fill("budget");
  await expect(page.getByTestId("search-result").first()).toBeVisible();
  await page.getByTestId("search-scope-action").click();
  await expect(page.getByTestId("search-none")).toBeVisible();
  await page.getByTestId("search-none-all").click();
  await expect(page.getByTestId("search-result").first()).toBeVisible();
});

test("the_day_headers_in_the_sidebar_stay_in_view", async ({ page, seed }) => {
  await seed([
    { id: "e2e-day-a", title: "Today one", state: "RENDERED", started_at: isoAt(0, 9) },
    { id: "e2e-day-b", title: "Yesterday one", state: "RENDERED", started_at: isoAt(1, 9) },
  ]);
  await gotoApp(page, "/");
  const header = page.getByTestId("timeline-day").first().locator("h2");
  await expect(header).toHaveCSS("position", "sticky");
});

test("the_night_hours_fold_and_the_choice_survives_a_reload", async ({ page, seed, request }) => {
  await seed([]);
  await request.put("/api/settings", { headers: HEADERS, data: { values: { "ui.calendar_span": "week" } } });
  await gotoApp(page, "/");
  const grid = page.getByTestId("calendar-timegrid");
  const day = page.locator("[data-testid=calendar-daybody]").first();
  const unfolded = (await day.boundingBox())!.height;

  await page.getByTestId("calendar-fold-nights").click();
  await expect(page.getByTestId("calendar-fold-nights")).toHaveAttribute("aria-pressed", "true");
  await expect(grid).toHaveAttribute("data-folded", "true");
  await expect(page.getByTestId("night-fold")).toHaveCount(2);
  // 08:00–18:00 at full height and two thin bands: well under half the unfolded day.
  expect((await day.boundingBox())!.height).toBeLessThan(unfolded / 2);

  await expect
    .poll(async () => (await (await page.request.get("/api/settings")).json()).config.ui.fold_nights)
    .toBe(true);
  await page.reload();
  await expect(page.getByTestId("calendar-timegrid")).toHaveAttribute("data-folded", "true");

  // The folded band itself opens the night again.
  await page.locator("[data-testid=night-fold][data-which=early]").click();
  await expect(page.getByTestId("calendar-timegrid")).not.toHaveAttribute("data-folded", "true");
  await expect(page.getByTestId("calendar-fold-nights")).toHaveAttribute("aria-pressed", "false");
});

test("a_night_with_a_meeting_in_it_stays_open_when_folded", async ({ page, seed, request }) => {
  await seed([{ id: "e2e-early", title: "Early call", state: "RENDERED", started_at: isoAt(0, 6), duration_s: 1800 }]);
  await request.put("/api/settings", {
    headers: HEADERS,
    data: { values: { "ui.calendar_span": "week", "ui.fold_nights": true } },
  });
  await gotoApp(page, "/");
  await expect(page.locator("[data-testid=calendar-event][data-meeting=e2e-early]")).toBeVisible();
  await expect(page.locator("[data-testid=night-fold][data-which=early]")).toHaveCount(0);
  await expect(page.locator("[data-testid=night-fold][data-which=late]")).toHaveCount(1);
});
