import { expect, gotoApp, isoAt, minutesAgo, test } from "./fixtures";

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
  await expect(card.getByTestId("stop-recording")).toBeVisible();
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
  await expect(card.getByTestId("meeting-state")).toHaveText("Transcribing");
  await expect(card.getByTestId("eta")).not.toHaveText("");
});

test("default_locale_is_english", async ({ page }) => {
  await gotoApp(page);
  await expect(page.getByTestId("nav-timeline")).toHaveAttribute("aria-label", "Timeline");
  expect(await page.evaluate(() => document.dir)).toBe("ltr");
});

test("locale_switch_no_reload", async ({ page }) => {
  await gotoApp(page, "/settings");
  await page.evaluate(() => {
    (window as unknown as { __marker: number }).__marker = 42;
  });
  await page.getByTestId("ui-language").selectOption("he");
  await expect(page.getByTestId("nav-timeline")).toHaveAttribute("aria-label", "ציר זמן");
  expect(await page.evaluate(() => document.dir)).toBe("rtl");
  expect(await page.evaluate(() => (window as unknown as { __marker?: number }).__marker)).toBe(42);
});

test("rtl_direction", async ({ page, seed }) => {
  await seed([{ id: "e2e-rtl", title: "פגישה עם Kubernetes", started_at: isoAt(0, 9) }]);
  await gotoApp(page, "/settings");
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

test("calendar_toggle_and_spans", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("timeline")).toBeVisible();

  // List is the default and the calendar is not mounted.
  await expect(page.getByTestId("calendar-controls")).toHaveCount(0);

  await page.getByTestId("view-calendar").click();
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

  // And back to the list.
  await page.getByTestId("view-list").click();
  await expect(page.getByTestId("calendar-controls")).toHaveCount(0);
});

test("calendar_choice_survives_a_reload", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("view-calendar").click();
  await page.getByTestId("span-month").click();
  await expect(page.getByTestId("calendar-monthgrid")).toBeVisible();

  await page.reload();
  await expect(page.getByTestId("calendar-monthgrid")).toBeVisible();

  await page.getByTestId("view-list").click(); // leave the default as it was
  await expect(page.getByTestId("calendar-controls")).toHaveCount(0);
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
