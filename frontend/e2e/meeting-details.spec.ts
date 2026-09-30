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
  await expect(dialog.getByTestId("meeting-info-title")).toHaveValue(
    /^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) .+ \d{1,2}:\d{2}/,
  );
  // The start floored to the quarter hour; the end half an hour later.
  const start = dialog.getByTestId("meeting-info-start");
  const end = dialog.getByTestId("meeting-info-end");
  await expect(start).toHaveValue(/:(00|15|30|45)/);
  await expect(end).toHaveValue(/:(00|15|30|45)/);

  await dialog.getByTestId("meeting-info-title").fill("Pricing review");
  await dialog.getByTestId("meeting-info-description").fill("Decide the new tier.");
  // Any time can be typed, not only the suggestions; until the end is set, it follows.
  await start.fill("9:10");
  await expect(end).toHaveValue("09:40");
  await end.fill("1025");
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
  expect(minutes).toBe(75);
  expect(new Date(saved.planned_start).getHours()).toBe(9);
  expect(new Date(saved.planned_start).getMinutes()).toBe(10);
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

test("the_details_keep_the_focus_where_the_user_types_and_reject_a_non_time", async ({ page, seed }) => {
  await seed([]);
  await gotoApp(page);
  await page.getByTestId("start-recording").click();
  const dialog = page.getByTestId("meeting-info");
  await expect(dialog.getByTestId("meeting-info-title")).toBeFocused();

  // The page refetches while recording; the focus must stay in the field being typed in.
  const description = dialog.getByTestId("meeting-info-description");
  await description.click();
  await description.pressSequentially("Still typing");
  await page.waitForTimeout(4000);
  await expect(description).toBeFocused();
  await expect(description).toHaveValue("Still typing");

  await dialog.getByTestId("meeting-info-start").fill("half past");
  await dialog.getByTestId("meeting-info-done").click();
  await expect(dialog.getByTestId("meeting-info-error")).toBeVisible();
  await dialog.getByTestId("meeting-info-start").fill("14:00");
  await dialog.getByTestId("meeting-info-end").fill("13:00");
  await dialog.getByTestId("meeting-info-done").click();
  await expect(dialog.getByTestId("meeting-info-error")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await page.getByTestId("stop-recording").click();
});
