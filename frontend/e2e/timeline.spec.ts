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
  const titles = await page.getByTestId("meeting-link").allTextContents();
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
  await expect(card.getByTestId("state-badge")).toHaveText("Transcribing");
  await expect(card.getByTestId("eta")).not.toHaveText("");
});

test("default_locale_is_english", async ({ page }) => {
  await gotoApp(page);
  await expect(page.getByTestId("nav-timeline")).toHaveText("Timeline");
  expect(await page.evaluate(() => document.dir)).toBe("ltr");
});

test("locale_switch_no_reload", async ({ page }) => {
  await gotoApp(page, "/settings");
  await page.evaluate(() => {
    (window as unknown as { __marker: number }).__marker = 42;
  });
  await page.getByTestId("ui-language").selectOption("he");
  await expect(page.getByTestId("nav-timeline")).toHaveText("ציר זמן");
  expect(await page.evaluate(() => document.dir)).toBe("rtl");
  expect(await page.evaluate(() => (window as unknown as { __marker?: number }).__marker)).toBe(42);
});

test("rtl_direction", async ({ page, seed }) => {
  await seed([{ id: "e2e-rtl", title: "פגישה עם Kubernetes", started_at: isoAt(0, 9) }]);
  await gotoApp(page, "/settings");
  await page.getByTestId("ui-language").selectOption("he");
  await page.getByTestId("nav-timeline").click();
  const link = page.getByTestId("meeting-link").first();
  const box = await link.boundingBox();
  const container = await page.getByTestId("timeline").boundingBox();
  expect(box).not.toBeNull();
  expect(container).not.toBeNull();
  // in RTL the card's text starts on the right side of the container
  expect(box!.x + box!.width).toBeGreaterThan(container!.x + container!.width / 2);
});
