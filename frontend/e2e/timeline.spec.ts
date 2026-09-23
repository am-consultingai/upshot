import { expect, gotoApp, isoAt, minutesAgo, test, gotoSettings } from "./fixtures";

test("timeline_renders_seeded", async ({ page, seed }) => {
  await seed([
    { id: "e2e-a1", title: "Day one morning", started_at: isoAt(2, 9) },
    { id: "e2e-a2", title: "Day one afternoon", started_at: isoAt(2, 15) },
    { id: "e2e-b1", title: "Day two", started_at: isoAt(1, 11) },
    { id: "e2e-c1", title: "Today early", started_at: isoAt(0, 8) },
    { id: "e2e-c2", title: "Today late", started_at: isoAt(0, 16) },
  ]);
  await gotoApp(page);
  await expect(page.getByTestId("meeting-card")).toHaveCount(5);
  const days = page.getByTestId("timeline-day");
  await expect(days).toHaveCount(3);
  const titles = await page.getByTestId("meeting-name").allTextContents();
  expect(titles[0]).toBe("Today late");
  expect(titles.at(-1)).toBe("Day one morning");
});

test("recording_card_live", async ({ page, seed }) => {
  await seed([
    { id: "e2e-live", title: "Live one", state: "RECORDING", started_at: minutesAgo(3) },
  ]);
  await gotoApp(page);
  const card = page.getByTestId("meeting-card").filter({ has: page.getByTestId("elapsed") });
  await expect(card).toHaveAttribute("data-state", "RECORDING");
  // Stop is in the row's menu (and in the recording bar), not a red button on the row.
  await card.hover();
  await card.getByTestId("meeting-menu").click();
  await expect(page.locator("[data-testid=overflow-menu-item][data-item=stop]")).toBeVisible();
  await page.keyboard.press("Escape");
  const first = await card.getByTestId("elapsed").textContent();
  await page.waitForTimeout(1200);
  expect(await card.getByTestId("elapsed").textContent()).not.toBe(first);
});

test("queue_card_shows_eta", async ({ page, seed }) => {
  await seed([
    {
      id: "e2e-queued",
      title: "Being transcribed",
      state: "TRANSCRIBING",
      started_at: isoAt(0, 12),
      jobs: { transcribe: "running" },
    },
  ]);
  await gotoApp(page);
  const card = page.getByTestId("meeting-card").filter({ hasText: "Being transcribed" });
  await expect(card.getByTestId("meeting-state")).toHaveText("transcribing");
  await expect(card.getByTestId("eta")).not.toHaveText("");
});

test("default_locale_is_english", async ({ page }) => {
  await gotoApp(page);
  await expect(page.getByTestId("nav-timeline")).toHaveAccessibleName("Library");
  expect(await page.evaluate(() => document.dir)).toBe("ltr");
});

test("locale_switch_no_reload", async ({ page }) => {
  await gotoSettings(page, "appearance");
  await page.evaluate(() => {
    (window as unknown as { __marker: number }).__marker = 42;
  });
  await page.getByTestId("ui-language").selectOption("he");
  await expect(page.getByTestId("nav-timeline")).toHaveAccessibleName("ספרייה");
  expect(await page.evaluate(() => document.dir)).toBe("rtl");
  expect(await page.evaluate(() => (window as unknown as { __marker?: number }).__marker)).toBe(42);
});

test("rtl_direction", async ({ page, seed }) => {
  await seed([{ id: "e2e-rtl", title: "פגישה עם Kubernetes", started_at: isoAt(0, 9) }]);
  await gotoSettings(page, "appearance");
  await page.getByTestId("ui-language").selectOption("he");
  await page.getByTestId("nav-timeline").click();
  const link = page.getByTestId("meeting-name").first();
  const box = await link.boundingBox();
  const container = await page.getByTestId("timeline").boundingBox();
  expect(box).not.toBeNull();
  expect(container).not.toBeNull();
  // in RTL the card's text starts on the right side of the container
  expect(box!.x + box!.width).toBeGreaterThan(container!.x + container!.width / 2);
});

/**
 * The calendar holds the detail side from the moment the screen opens. There used to be a
 * List/Calendar toggle above the meeting list, which chose between the column and the
 * pane beside it — two views of one collection with a switch insisting only one could be
 * shown. The list is the column; the calendar is the pane.
 */
test("the_calendar_is_the_detail_side_and_the_spans_work", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("timeline")).toBeVisible();

  // No toggle, and the calendar is already there.
  await expect(page.getByTestId("view-toggle")).toHaveCount(0);
  await expect(page.getByTestId("calendar-controls")).toBeVisible();
  // Week is the default span, so the time grid renders seven day columns.
  await expect(page.getByTestId("calendar-timegrid")).toBeVisible();
  await expect(page.getByTestId("calendar-daycolumn")).toHaveCount(7);

  await page.getByTestId("span-day").click();
  await expect(page.getByTestId("calendar-daycolumn")).toHaveCount(1);

  await page.getByTestId("span-month").click();
  await expect(page.getByTestId("calendar-monthgrid")).toBeVisible();
  const cells = await page.getByTestId("calendar-daycell").count();
  expect(cells % 7).toBe(0);
  expect(cells).toBeGreaterThanOrEqual(28);

  // Navigation moves the period and Today comes back.
  const period = await page.getByTestId("calendar-period").textContent();
  await page.getByTestId("calendar-next").click();
  await expect(page.getByTestId("calendar-period")).not.toHaveText(period ?? "");
  await page.getByTestId("calendar-today").click();
  await expect(page.getByTestId("calendar-period")).toHaveText(period ?? "");
});

test("calendar_span_survives_a_reload", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("span-month").click();
  await expect(page.getByTestId("calendar-monthgrid")).toBeVisible();

  await page.reload();
  await expect(page.getByTestId("calendar-monthgrid")).toBeVisible();

  await page.getByTestId("span-week").click(); // leave the default as it was
  await expect(page.getByTestId("calendar-timegrid")).toBeVisible();
});

test("recording_shows_a_live_waveform_on_every_page", async ({ page, seed }) => {
  await seed([]);
  await gotoApp(page);
  await expect(page.getByTestId("recording-bar")).toHaveCount(0);

  await page.getByTestId("start-recording").click();
  const bar = page.getByTestId("recording-bar");
  await expect(bar).toBeVisible();

  // The synthetic source is a tone: a working waveform paints coloured bars, not just the
  // grey centre lines.
  const coloured = () =>
    page.getByTestId("recording-waveform").locator("canvas").evaluate((node) => {
      const canvas = node as HTMLCanvasElement;
      const data = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, canvas.height).data;
      let count = 0;
      for (let index = 0; index < data.length; index += 4) {
        const [r, g, b, a] = [data[index], data[index + 1], data[index + 2], data[index + 3]];
        if (a > 0 && Math.max(r, g, b) - Math.min(r, g, b) > 60) count += 1;
      }
      return count;
    });
  await expect.poll(coloured, { timeout: 10_000 }).toBeGreaterThan(50);

  // Not only on the timeline.
  await page.getByTestId("nav-settings").click();
  await expect(bar).toBeVisible();

  await bar.getByTestId("recording-bar-stop").click();
  await expect(page.getByTestId("recording-bar")).toHaveCount(0);
});

test("a_dead_backend_says_so_instead_of_something_went_wrong", async ({ page }) => {
  await gotoApp(page);
  // What a crashed application looks like from the page: requests stop being answered.
  await page.route("**/api/status", (route) => route.abort());
  await expect(page.getByTestId("connection-lost")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("connection-lost")).toContainText("run-app.cmd");
});

/**
 * Clicking a meeting opens it. Obvious, and untested until someone reported that
 * it did not: every other spec reached a meeting by URL, by keyboard or through
 * the palette, so the one route a person actually uses was the one route nothing
 * covered.
 */
test("clicking_a_meeting_opens_it", async ({ page, seed }) => {
  await seed([
    { id: "click-1", title: "Openable", state: "RENDERED", started_at: isoAt(0, 20) },
  ]);
  await gotoApp(page, "/");
  await expect(page.getByTestId("calendar-controls")).toBeVisible();

  await page.getByTestId("meeting-link").first().click();
  await expect(page.getByTestId("meeting-page")).toHaveAttribute("data-meeting-id", "click-1");
});

/**
 * And it opens from the calendar too. The calendar shares the detail side with
 * whatever is open, and it used to hold that side unconditionally — so the click
 * navigated, the URL changed, and the calendar carried on being drawn over the
 * meeting. Nothing looked broken; it just did nothing.
 */
test("clicking_a_meeting_opens_it_from_the_calendar_too", async ({ page, seed }) => {
  await seed([
    { id: "click-2", title: "Openable from calendar", state: "RENDERED", started_at: isoAt(0, 20) },
  ]);
  await gotoApp(page, "/");
  await expect(page.getByTestId("calendar-controls")).toBeVisible();

  await page.getByTestId("meeting-link").first().click();
  await expect(page.getByTestId("meeting-page")).toHaveAttribute("data-meeting-id", "click-2");
  await expect(page.getByTestId("calendar-controls")).toHaveCount(0);
});

/** A narrow window hides the list rather than the meeting. */
test("a_narrow_window_still_opens_the_meeting", async ({ page, seed }) => {
  await seed([
    { id: "click-3", title: "Narrow", state: "RENDERED", started_at: isoAt(0, 20) },
  ]);
  await page.setViewportSize({ width: 900, height: 800 });
  await gotoApp(page, "/");
  await page.getByTestId("meeting-link").first().click();
  await expect(page.getByTestId("meeting-page")).toHaveAttribute("data-meeting-id", "click-3");
  await page.setViewportSize({ width: 1280, height: 900 });
});
