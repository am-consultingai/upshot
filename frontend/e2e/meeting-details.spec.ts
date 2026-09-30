import { expect, gotoApp, test } from "./fixtures";

/**
 * Record a meeting: the recording starts as always, and a dialog asks for the meeting's
 * details alongside it — title, description, start and end, all optional (2026-09-30).
 */
test("recording_asks_for_the_details_and_done_saves_them", async ({ page, seed }) => {
  await seed([]);
  await gotoApp(page);
  await page.getByTestId("start-recording").click();

  const dialog = page.getByTestId("meeting-info");
  await expect(dialog).toBeVisible();
  // Recording already: the dialog does not hold it up.
  await expect(page.getByTestId("recording-bar")).toBeVisible();

  // The default name: weekday, date and time.
  await expect(dialog.getByTestId("meeting-info-title")).toHaveValue(/^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) .+ \d{1,2}:\d{2}/);
  // The start floored to the quarter hour; the end offered as a duration with its time.
  const startText = await dialog.getByTestId("meeting-info-start").locator("option:checked").textContent();
  expect(startText).toMatch(/:(00|15|30|45)/);
  const endText = await dialog.getByTestId("meeting-info-duration").locator("option:checked").textContent();
  expect(endText).toMatch(/^30 min \(\d{1,2}:\d{2}.*\)$/);

  await dialog.getByTestId("meeting-info-title").fill("Pricing review");
  await dialog.getByTestId("meeting-info-description").fill("Decide the new tier.");
  await dialog.getByTestId("meeting-info-duration").selectOption("45");
  await dialog.getByTestId("meeting-info-done").click();
  await expect(dialog).toHaveCount(0);

  // Stop, then the meeting carries what was typed.
  await page.getByTestId("stop-recording").click();
  await expect(page.getByTestId("recording-bar")).toHaveCount(0);
  const meetings = await (await page.request.get("/api/meetings")).json();
  const saved = meetings.meetings.find((m: { title: string }) => m.title === "Pricing review");
  expect(saved).toBeTruthy();
  expect(saved.description).toBe("Decide the new tier.");
  const minutes = (new Date(saved.planned_end).getTime() - new Date(saved.planned_start).getTime()) / 60000;
  expect(minutes).toBe(45);
  expect(new Date(saved.planned_start).getMinutes() % 15).toBe(0);
});

test("closing_the_details_keeps_nothing_and_the_menu_opens_them_later", async ({ page, seed }) => {
  await seed([]);
  await gotoApp(page);
  await page.getByTestId("start-recording").click();
  await expect(page.getByTestId("meeting-info")).toBeVisible();
  await page.getByTestId("meeting-info-cancel").click();
  await page.getByTestId("stop-recording").click();
  await expect(page.getByTestId("recording-bar")).toHaveCount(0);
  const meetings = await (await page.request.get("/api/meetings")).json();
  const last = meetings.meetings[0];
  expect(last.description ?? null).toBeNull();
  expect(last.planned_start ?? null).toBeNull();

  await gotoApp(page, `/m/${last.id}`);
  await page.getByTestId("overflow-menu-trigger").click();
  await page.locator('[data-item="details"]').click();
  await expect(page.getByTestId("meeting-info")).toBeVisible();
  await page.getByTestId("meeting-info-description").fill("Filled in afterwards.");
  await page.getByTestId("meeting-info-done").click();
  await expect(page.getByTestId("meeting-description")).toHaveText("Filled in afterwards.");
});
